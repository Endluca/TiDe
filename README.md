# TiDe｜新师训练营（运营端 + 教师端）

面向新外教 30 天试用期的成长系统。仓库同时包含运营端和教师端：系统把教师、课程、
履约、课堂质量、用户反馈、产能和成长任务事实，转成可解释的积分、资格、任务和运营行动。

当前状态：**公司测试环境可运行，尚未生产上线。**

## 业务方本地开发启动

前提：

- Python 3.9 或更高版本；
- Node.js 18 或更高版本；
- 能访问公司测试数据库的网络；
- 单独收到的 `TiDe.env` 和运营登录账号。

```bash
git clone git@flow.51talk.biz:ai-efficiency/Tide_teachers_camp.git
cd Tide_teachers_camp

./scripts/setup.sh /安全路径/TiDe.env
./scripts/start.sh /安全路径/TiDe.env
```

首次启动需要加载测试数据，通常要等待几十秒。看到启动成功提示后打开：

- Web App：`http://127.0.0.1:5174`
- API：`http://127.0.0.1:8010`
- API 文档：`http://127.0.0.1:8010/docs`

按 `Ctrl+C` 会同时停止前端、后端和积分结算进程。日志位于 `.runtime/`，不会进入 Git。

`TiDe.env` 只通过安全渠道单独交付，不得上传、转发到公开群或提交到仓库。默认配置连接
独立的业务交接测试库；页面中的修改会真实写入该测试库，但不会影响生产系统。

## 当前页面

- 经营总览
- 待办处置
- 任务进展
- 触达记录
- 教师档案
- 课程证据
- 任务规则
- 积分与门槛
- 操作审计

完成登录后，系统先展示运营台和各页空状态，不自动读取业务数据。只有用户主动点击当前页
的“更新”按钮，才读取该页最新数据；已访问页面在当前会话中保留，切换页面不会偷偷刷新。

## 当前业务口径

- `task_assignments` 是任务实例和状态的唯一事实表。
- 新教师首次进入时，系统幂等初始化当前 9 项必修成长任务，默认状态为 `ASSIGNED`。
- 当前任务目录只有 9 个固定成长任务和 5 个个性化改善任务；系统不得自由发明任务。
- 教师端只更新已有任务的执行状态，不能写积分、总分或资格。
- `task_assignments.why` 是教师端外显原因，固定和个性化任务均必须使用英文；个性化任务的数据库原值包含 `Evidence:` 最小证据摘要，读取时不再二次拼接；上游原始中文值只保留在证据快照。
- 个性化任务的 `task_assignments.display_title` 是英文数据库事实；运营端和教师端直接读取，不在页面或 API 层转换。
- `notifications.payload.title/body` 是触达展示的英文数据库事实，`body` 内含 `Evidence:` 关键证据摘要；运营端和教师端直接读取，不在展示层翻译或补写。
- 可靠性分：课程明细派生的完美完课数 `perfect_cnt × 4` + Peak 完课数 `peak_completed_cnt × 2`。
- 课堂质量：每节课的未开摄像头、CPU 占用过高、网络延迟过高三个字段都明确为 0 时，硬件质量加 2 分；任一字段为 1 或为空均不加分，空值同时标记为 `SOURCE_MISSING`。
- 逐课 `is_perfect`：课程状态为 `end`、迟到为 0、早退为 0；每节达标课程在可靠性维度加 4 分，并进入 `lesson_total_score`。教师维度 `perfect_cnt` 按去重 `source_appoint_id` 汇总。
- 用户反馈分：好评次数 × 5 + 教师维度 `feedback_favorite_cnt` × 5；课程级收藏按
  `teacher_id + student_id_hash` 去重，仅最早一节有效完课的收藏归因加 5 分，后续
  收藏课仍保留收藏事实但该项加分为 0；15 日复约只保留事实，不计分。
- 供给分：`peak_slot_cnt` 首次达到 40 时加 10 分并锁定。
- 固定成长任务：按当前 9 项必修任务的完成状态与配置分值累加，最高 30 分。
- 出营资格：当前 9 项必修任务全部完成、L0 投诉为 0、raw 总分不低于 100。
- 金牌资格：满足当前出营条件、raw 总分不低于 200、`late_cnt <= 1`、
  `early_cnt = 0`、`absent_cnt = 0`。
- 新积分规则发布时，在同一发布事务内按新规则全量重算当前教师积分；重算失败则发布失败。
- 教师一旦获得出营或金牌资格，后续数据修正或新规则降分都不撤回已获得资格。
- 个性化任务由确定性规则触发；当前规则不依赖 OpenAI Key。
- 重复差评任务仍使用稳定码 `P-FB-NEGATIVE`；其中“灯光过暗/亮”“环境乱/灯光差”由触发中心写入稳定执行变体，教师端复用 G04 四项标准进行单步拍照 AI 检测。该个性化变体每次只判定本次照片，本次四项 PASS 即完成；照片和审核明细不保留、历史 PASS 不复用。G04 原有保存与复用行为不变。其他差评标签不做模糊匹配或统一切换。

完整口径见 [数据与积分规则](docs/数据与积分规则.md)。

## 给业务方和 AI 的背景资料

先阅读 [project-context/README.md](project-context/README.md)。其中包含：

- 产品目标与页面边界；
- 当前页面与已确认的交互约束；
- 页面、接口和后端规则的代码映射；
- 运行、真实数据和敏感信息边界。

开发代理还应遵守 [AGENTS.md](AGENTS.md)。

## 目录

```text
Tide_teachers_camp/
├── backend/           # 运营端 FastAPI、PostgreSQL、Alembic、积分与任务规则
├── frontend/          # React 运营 Web App
├── teacher/           # 教师端 NestJS API、React Web App 及其文档
├── contracts/         # 两端共享任务和课程数据契约
├── deploy/combined/   # 两端同机、不同域名的联合部署入口
├── gaea/              # application / DTS 双构建图与三个 Gaea 项目的统一构建入口
├── docs/              # 架构、数据、积分、认证和配置说明
├── project-context/   # 业务方与 AI 的项目背景
└── scripts/           # 运营端一键安装和启动
```

教师端的独立开发、测试和构建命令见
[`teacher/README.md`](teacher/README.md)。根目录与 `teacher/` 各自保留技术栈和迁移链，
但通过同一 PostgreSQL 业务事实与共享契约协作。

## 数据与系统边界

- PostgreSQL 是运行事实源，Schema 只通过 Alembic 变更。
- 当前交接测试库只包含显式测试 Seed，不是生产日更数据。
- 当前代码迁移 head 为 public `20260818_62_dts_claim_idx` 与 teacher
  `0041_crm_sso_hybrid`，最终 teacher canonical 账本为 36 条，其中
  `20260811_51_g01_tesol_only` / `0033_g01_tesol_only`
  将 G01 收窄为 TESOL-only，`20260811_54_g04_remove_device_check` /
  `0037_g04_remove_device_check` 将 G04 收敛为照片审核与课件准备两模块，
  `20260811_55_source_wide_v12` 将教师源表收敛为确认的 55 列，
  `20260811_56_p_fb_negative_copy` / `0038_personalized_environment_photo`
  追加个性化环境拍照，`20260811_57_g02_document` / 0039 / 0040 发布 G02
  原生政策文档与阅读状态，0041 新增 CRM SSO 混合认证结构；public 59 汇合
  release 内容链与 ACL/DTS 分支，public 60 再增加海外目标库的国内学生隐私
  fail-closed Trigger，public 61 只原位更新经审核的教师英文文案，不修改任务身份、分值、
  assignment 或状态；public 62 为 `PENDING` 与到期 `RETRY` 脏键分别增加匹配领取顺序的
  并发部分索引，避免投影每领取一个键都全表扫描和排序。
  公司 TEST 库 `tit_growth_test_v2` 已于 2026-08-11 按七阶段顺序受控升级至
  public `20260812_56_lean_roles` 与 teacher `0037_g04_remove_device_check`，
  teacher 为精确 32 条 canonical 账本；升级保留 public
  `20260810_50_g04_sections` / teacher `0032_first_login_onboarding` 中间切换点，
  G01、G04 与源宽表均已应用对应契约，但这不是 public 62 / teacher 0041 的完成证明。
  重建前旧库封存为
  `tit_growth_test_v2_pre0030_20260810`，仅保留 DBA 回滚连接。代码目标结构中
  `teacher_source_wide` 为确认映射的 53 个教师字段加 2 个可空教师资料状态字段（G01 只消费 TESOL），
  `lesson_source_wide` 严格对应 CSV 课程 23 列（无“是否复约”）；两表均不增加更新时间、版本、哈希或同步批次字段。
  rev56 只更新稳定 `P-FB-NEGATIVE:v1` 的 How 与完成标准；teacher `0038_personalized_environment_photo` 保留既有身份与进度并发布个性化拍照执行配置，`0041_crm_sso_hybrid` 不修改任务、积分或课程事实。旧库 `tit_growth_test` 未原地改造。
- 两张源表提交真实变化时，Trigger 只记录字段差异 Outbox；独立 SourceWide Worker
  默认每 3 秒轮询，按变化字段定位受影响教师，在另一个事务内幂等更新逐课结果、积分、
  任务触发和资格。失败事件保留重试，不在源表事务里执行复杂计算。
- 原始 Excel、学生身份、数据库 dump、环境文件和日志都不进入 Git。
- “任务已创建”不等于“通知已送达”；“测试环境可运行”不等于“生产上线”。
- 当前运营 API 的公开读写路径均直接使用 PostgreSQL 事务/查询，可运行多个 API Worker；
  本地一键启动中的运营 API 默认单 Worker，便于开发排查。
- 国内/海外 DTS 的字段映射、Avro 消费、持久事件账本、白名单当前态、脏键、数据库位点和 23/55 字段宽表投影代码已实现；除真实消费组 ID（sid）必须在部署时从对应订阅的“数据消费”页注入外，其余已确认的非敏感参数已固化。截至 2026-08-19，系统库已由现场读回确认为 public `20260818_62_dts_claim_idx`；本轮不再需要 public Alembic 迁移，但应用发布、direct 模式切换及执行计划/吞吐复验仍是独立门禁，不能把数据库到 head 写成 `pre-tida-camp` 全流程可用。部署边界现已收紧为：海外消费者运行在新加坡，国内消费者通过“AI 效率中心”团队的独立 Gaea 项目 `tida-camp-dts-dom` 运行在中国大陆集群；平台项目选择和 `TIT_DTS_EXECUTION_REGION=cn` 都必须在新 Pod 上读回，不能只凭变量声明推断地理放置。国内消费者在构造任何发往海外 PostgreSQL 的 SQL 参数前，使用仅注入国内项目的密钥把原始学生 ID 转为 `dom:v1:<HMAC-SHA256>`，海外项目和海外数据库均不得持有该密钥或原始国内学生 ID；稳定 token 仍按伪名数据受限管理。默认及正式环境仍固定 `sslmode=verify-full`。当前 `tide_system_test` PRE 固定专线端点允许用既有 `sslmode=disable` 受控例外；专线只限制网络路径，并不加密 PostgreSQL 流量。该例外不得扩展到其他端点、库或正式环境，数据库启用 TLS 后必须恢复 `verify-full`。当前仍无 direct 新版本在国内/海外 Pod 的成功 readiness/heartbeat、双流 checkpoint、目标写入或真实字段对账，不能视为链路联调完成；外部生产接入、真实通知回执、监控、备份和回滚仍待完成。
- 2026-08-19 源码新增未发布的 `TIT_DTS_PROJECTION_MODE=direct` 显式模式：事件在 checkpoint 事务内直接更新 23/55 宽表，不写业务事件账本、通用源当前态镜像或脏键；只在现有 `dts_source_rows` 保留小型 `dom_complaint_cate` 参考字典和国内 HMAC 指纹契约。direct 允许从中途边界只接增量：只有教师/课程 INSERT 创建主行，无法命中宽表的历史 UPDATE、DELETE 和子事件记为 ignored 并推进 checkpoint；结构、隐私、计数及数据库错误仍失败关闭。默认仍是 `queued`，现有运行配置和“仅海外投影 owner”边界不自动改变。只有完成双 DTS 同边界重置、宽表清理/基线、投诉分类字典基线和新规则对账后，才允许国内、海外同时显式启用 direct。规则见 [`docs/DTS事件直接投影规则.md`](docs/DTS事件直接投影规则.md)。

## Gaea 部署骨架（常驻双入口、临时诊断模块、三项目）

[`gaea/gaea.yml`](gaea/gaea.yml) 声明 `application`、`dts-ingest` 和临时
`dts-diagnose` 三个构建模块。
`application` 构建运营 React、教师 React、运营 FastAPI 和教师 NestJS，并用 s6-overlay 在
一个 Pod 中管理运营 API、教师 API、教师 Nginx、积分结算 Worker 与 SourceWide Worker 五个
进程；`dts-ingest` 不构建两个前端、教师 NestJS 或 Nginx，它由 Python 进程持有数据库、国内
HMAC、账本和投影事务，并拉起使用阿里云官方诊断包内 Kafka Java Client 1.0.0 的受控子进程
负责 Kafka 传输。Java 以受消息数和 8 MiB 双重限制的批次交付原始 Avro 消息；Python 先完成
整批解码、国内 HMAC 和 PostgreSQL 原子事务，成功后才返回一个 `DURABLE_ACK_BATCH`，Java
随后仅对最后一条连续 ADVANCE 请求 SDK checkpoint。任一数据库错误都不会产生 ACK。`dts-diagnose` 固定封装阿里云
排错文档链接的 Java 8 官方诊断 JAR，仅在
国内 PRE 原项目中临时替换 `dts-ingest` 做协议 A/B，不是第四个常驻项目。根
[`gaea/Dockerfile`](gaea/Dockerfile) 暂时保留为 application 的兼容入口：

```bash
docker build -f gaea/Dockerfile -t tide-camp:gaea .
docker build -f gaea/application/Dockerfile -t tide-camp:gaea .
docker build -f gaea/dts-ingest/Dockerfile -t tide-camp-dts:gaea .
docker build -f gaea/dts-diagnose/Dockerfile -t tide-camp-dts-diagnose:gaea .
```

现有 application 项目可暂时继续使用根兼容入口，切换多模块后选择 `application`，并支持整套
Pod 设置为 `2` 个或更多副本；海外、国内 DTS 分别使用独立 Gaea 项目，海外项目必须选择新加坡
数据中心，国内项目必须选择中国大陆数据中心，两个项目都选择同一个
`dts-ingest` 构建模块并各自保持 1 个
副本。两套 DTS 项目的镜像内容相同，但 Gaea 仍会为两个项目分别构建和推送；broker、消费组、
账号、密码和接入位点通过彼此隔离的运行变量注入。国内 HMAC 密钥只注入国内项目；国内项目固定
关闭投影，海外项目是唯一投影 owner。数据库连接默认和正式环境使用 `verify-full`；当前固定
`tide_system_test` PRE 专线例外可由国内、海外 DTS 复用既有两项覆盖，不能把通道隔离写成传输加密。
模块选择是项目构建配置，
不是运行时环境变量，也不会自动把项目放到正确数据中心。
诊断模块内置 DTS SDK 1.4.0 与 Kafka Java Client 1.0.0，完整输出 SDK/Kafka 协议日志和解码记录；它会推进所选
消费组位点。该路径已在国内 PRE 实际读取并解码 DTS 记录，因此正式 `dts-ingest` 复用同一
`DefaultDTSConsumer → KafkaRecordFetcher → UserRecordGenerator → EtlRecordProcessor` 官方主流程，
但用受控 listener 取代会打印业务数据的诊断 Main。使用前必须留底数据库 checkpoint、停止正式消费者，出现首条 HEARTBEAT 或
明确错误后立即停止并切回 `dts-ingest`。完整步骤、JAR 来源与 SHA-256 见
[`gaea/README.md`](gaea/README.md) 和 [`gaea/dts-diagnose/SOURCE.md`](gaea/dts-diagnose/SOURCE.md)。
每个 DTS 进程启动时先只读校验目标库，再启动官方 SDK 主流程；首条官方 `UserRecord` 到达受控
listener 后才证明 Kafka 消费与 DTS 解码路径已建立。Java 按记录数和 8 MiB payload 双重限界逐条
发送 EVENT 帧；Python 在任何 SQL 前完成整批解码与国内 HMAC，再以一个 PostgreSQL 事务持久化
连续批次。只有该事务提交后才返回一次批量 durable ACK，Java 才对最后一条连续 ADVANCE 调用官方
`DefaultUserRecord.commit()`；REPLAY 不调用。国内进程
在该启动门禁成功后、readiness 之前幂等登记一条只含契约版本和 HMAC
密钥 fingerprint 的受限状态行；该行不含密钥或学生标识。TCP 失败输出 `DTS_BROKER_TCP_*`
稳定错误码仍由保留的 `kafka_python` 回退模式提供；正式 Gaea 默认的 Java transport 会把官方
Kafka 网络时间线写到 stderr，stdout 只用于 Java/Python NDJSON 协议。Java 启动或运行错误只向
Python 返回稳定错误码，不把密码或原始 Avro 写入协议日志。

仅当显式设置 `TIT_DTS_TRANSPORT=kafka_python` 做回退诊断时，旧 Kafka 启动日志先输出脱敏的客户端契约摘要（客户端版本、
API 自动协商模式、SASL 协议、partition 和有界超时），再依次输出 `consumer_open`、`bootstrap_auth`、
`topic_metadata`、`partition_check`、`advertised_broker_auth`、`group_coordinator`、
`coordinator_auth`、`offset_fetch`，并按实际位点路径继续输出 `offsets_for_times`、
`beginning_offsets`、`end_offsets` 的固定 `begin/ok/fail` 阶段。`topic_metadata` 会真实发送与
`kcat -L -t <topic>` 同类语义的单 Topic Metadata 请求，随后由 `partition_check` 验证 partition 0；该请求保留真实 `ApiVersions` 自动协商，但仅将 Metadata API（key 3）上限收敛为 v5，以对齐已能成功消费的官方 Java 1.0 诊断客户端的 Metadata 版本边界；其他 Kafka API 不降级。`kafka_client_config` 会读回该策略，`topic_metadata` 在完成版本选择后记录服务端声明的 Metadata 版本范围与实际选用版本。整个过程不引入第二套客户端或带密码
配置文件。`consumer_open` 会先对 bootstrap 连接真实发送 `ApiVersions` 自动协商客户端兼容协议，
再完成该连接的 SASL；日志只把 kafka-python 推断结果记为“协议兼容版本”，不冒充 DTS Broker 的
精确版本。阿里云文档中的 `0.11–2.7` 是受支持的 Kafka **客户端**版本范围，不是要求调用方固定
Broker API 2.7；实现因此让每个 fresh consumer 都重新协商。参见[阿里云 DTS Kafka 客户端说明](https://help.aliyun.com/zh/dts/user-guide/use-a-kafka-client-to-consume-tracked-data)。
后续 `bootstrap_auth` 复核已认证连接，通常显示 `connection_reused=true`。协议分段
适配器只接受锁定的 kafka-python 2.2.20，依赖漂移会在发送 Kafka 凭据或协议请求前失败关闭。
连接阶段输出固定状态路径以及 TCP、协议版本、SASL 的安全布尔证据。完成或失败
阶段带 `elapsed_ms`；失败只输出白名单 `error_type`、稳定
`error_code` 和 `retriable`；Kafka 阶段的该字段表示库级重试语义，容器还会经过应用暂态白名单再决定
是否重试。上述启动诊断不输出 endpoint、账号、消费组、密码、异常正文或堆栈。
代码会隔离 kafka-python 原生日志，只保留这些结构化诊断，因为 SASL 调试报文可能包含认证字节；
`consumer_open=ok` 现在必须同时具备成功 ApiVersions 响应和 bootstrap SASL 连接证据；它仍不
证明 Topic、advertised leader、消费组协调器或 offset 可用，后续阶段必须继续通过。消费者使用
手工 partition assignment，不执行 `JoinGroup`；消费组可用性以 `group_coordinator` 和
`offset_fetch` 为准。
上述 `kafka_python` 回退启动阶段默认按同一个 15 秒 deadline 收紧剩余请求超时；仅该回退门禁可分别通过
`TIT_DTS_KAFKA_STARTUP_REQUEST_TIMEOUT_MS` 与
`TIT_DTS_KAFKA_STARTUP_API_VERSION_AUTO_TIMEOUT_MS` 在 `1–120000ms` 内调整。整轮共享 deadline
取两者较大值；每个 Kafka 请求取“自身配置上限”和“当时整轮剩余预算”的较小值，因此较晚阶段
可能短于配置值。脱敏客户端摘要明确输出三个超时/预算 `configured_*` 上限与独立的 Metadata 版本上限，每条 phase 再输出当时的
`remaining_probe_budget_ms` 以及两个 `effective_*` 值；超时失败时动态值安全收敛为 `0`。
这只是 kafka-python 阻塞 SASL/DNS 调用遵守超时的协作式预算，不是可强制终止进程的绝对
wall-clock 上限；这两个变量不传给官方 Java transport。Java 1.0 使用官方客户端自身的请求
超时和协议协商。数据库批量 durable ACK 之后只表示官方 SDK 已接受 checkpoint 请求；SDK 后续异步
推进 Kafka 位点且没有同步成功回执，因此 PostgreSQL 事件账本与 `next_offset + source_timestamp`
始终是恢复权威，不能把 `SDK_CHECKPOINTS_ACCEPTED` 写成 broker 已同步提交。
`--watch` 容器对明确白名单内的启动暂态网络、Kafka/数据库连接和激活依赖失败，以及稳态
`official_java` 的暂态断线/超时，都会关闭当轮资源并使用全新连接，
默认按 `15/30/60` 秒有界退避重跑完整启动门禁，不再靠进程退出制造 CrashLoop；重试期间不写 readiness 或
heartbeat。认证/授权、配置、Schema/ACL、隐私/HMAC 和 offset 不变量错误仍失败退出。这些检查在
每次容器进程启动或暂态重试时执行，不在镜像构建或周期健康检查中重复执行。Pod Ready 只表示
“具备开始消费的条件”，不表示已经完成 CDC 接入或字段对账。

application 的两个 Worker 分别通过 PostgreSQL session advisory lock 保持逻辑单活，未持锁的
standby 仍刷新本 Pod heartbeat，并用数据库探测维持 readiness。教师全局调度使用
`tide.job_leases`；G04 图片审核属于任务提交校验，不再运行独立照片 Worker。

Gaea application 的非敏感运行参数可集中上传为配置文件并挂载到
`/deployments/config/application.env`，平台保留
`TIT_PROCESS_PROFILE=application`，并设置
`TIT_RUNTIME_ENV_FILE=/deployments/config/application.env`。模板见
[`gaea/application/application.runtime.env.example`](gaea/application/application.runtime.env.example)；
平台环境变量优先，数据库 URL、密码、JWT/API Key 继续走 Gaea 敏感变量，DTS 参数禁止混入。
配置文件不热加载，改版本后必须执行 `RollingUpdate`。完整迁移步骤与失败关闭规则见
[`gaea/README.md`](gaea/README.md)。

多副本的私有文件首选 OSS；`FILE_STORAGE_PROVIDER=LOCAL` 只允许所有 Pod 共享同一块
`ReadWriteMany (RWX)` 卷。视频预热脚本的本地幂等账本若被执行，也必须使用跨执行节点可见
的 RWX 状态目录；Worker heartbeat 必须留在各 Pod 的 `/tmp`，不能共享。完整环境变量、
双域名、DTS 模块选择、健康检查、连接预算和发布验收见 [Gaea 部署说明](gaea/README.md)。逻辑服务与数据库
角色仍然独立；TiDe Alembic 和教师端 migration 仍须作为发布前独立作业执行。application 的单容器形态
只用于受控 TEST：同一 UID 的进程仍能接触整套容器密钥，不具备生产级秘密隔离。
教师 Web 固定请求同源 `/api` 并由 Nginx 代理，后续更换教师域名不再需要重建前端镜像。

## 现有分离式生产部署骨架

原有 Compose 继续保留为分离式部署和联合部署参考；它用于构建可追溯产物，不代表公司
生产资源已经开通，也不是 Gaea UI/API 合一镜像的启动方式：

```bash
export TIDE_RUNTIME_ENV_FILE=/安全路径/TiDe.runtime.production.env
export TIDE_MIGRATION_ENV_FILE=/安全路径/TiDe.migration.production.env
export TIDE_MIGRATION_EXPECTED_DATABASE=tit_growth
export TIDE_OPS_HOST=tit-growth.example.com

# 默认只监听本机网关；如公司网关在另一台机器，只填写本机明确的私网 IP。
export TIDE_GATEWAY_BIND_ADDRESS=127.0.0.1
# 必须选择与宿主机及现有 Docker 网络都不重叠的专用网段，并在启动前核对。
export TIDE_RUNTIME_SUBNET=172.31.254.0/24
export TIDE_WEB_PROXY_IP=172.31.254.10

# 先失败关闭校验环境文件、数据库角色、绑定地址和内部网段，再展开 Compose。
python3 scripts/preflight_production.py
docker compose -f docker-compose.production.yml --profile migration config --quiet

# 校验通过后单独执行迁移并检查结果。
docker compose -f docker-compose.production.yml --profile migration run --rm migrate

# 首次部署时单独创建启动运营账号。密码仅注入本次命令，不写入环境文件。
TIT_BOOTSTRAP_USERNAME='<运营账号>' \
TIT_BOOTSTRAP_PASSWORD='<至少 12 位的强密码>' \
docker compose -f docker-compose.production.yml --profile migration run --rm \
  -e TIT_BOOTSTRAP_USERNAME -e TIT_BOOTSTRAP_PASSWORD \
  migrate python scripts/bootstrap_operator.py

# 启动两个 API Worker、两个后台 Worker 和静态 Web 服务。
docker compose -f docker-compose.production.yml up -d api score-settlement source-wide web
```

- `backend/Dockerfile` 使用非 root 用户运行 FastAPI，默认 2 个 Worker；
- `frontend/Dockerfile` 产出静态资源，Nginx 同源代理 `/api`；
- `TIDE_RUNTIME_ENV_FILE` 使用受限运行角色 `tit_growth_app`，同时供运营 API、积分结算和
  SourceWide Worker 使用；三者属于同一 TiDe 后端信任边界。`TIDE_MIGRATION_ENV_FILE`
  使用现有管理账号 `tide_sys_admin`，只在受控发布窗口执行两条迁移链和只读契约探针；
  两个文件不得复用，数据库凭据只由部署环境注入，不能复制进镜像；
- 启动运营账号属于一次性管理动作，必须复用迁移/管理凭据执行；运行角色只有账号读取和
  自身密码哈希列更新权限，不能创建账号或授予角色；
- 同源代理会把 Web App 域名作为 API 的 `Host`。`TIDE_OPS_HOST` 必须同时出现在运行
  环境文件的 `TIT_ALLOWED_HOSTS` 中，Compose 会用它覆盖 `TIT_HEALTHCHECK_HOST`；
  同源部署的 `TIT_ALLOWED_ORIGINS` 保持为空；
- Web Nginx 是 API 唯一可信 HTTP 代理，`TIDE_WEB_PROXY_IP` 与
  `TIDE_RUNTIME_SUBNET` 都是生产必填项。所选网段必须先通过
  `docker network ls` / `docker network inspect` 和宿主路由核对不重叠，API 只信任
  这个精确容器 IP，不能改回整个 Docker 网段；
- HTTPS 在公司网关/负载均衡终止。原点 Nginx 会丢弃所有入站 `X-Real-IP`、
  `X-Forwarded-For` 与 `X-Forwarded-Proto`，只把直接连接的网关地址传给 API，并将
  协议固定为生产 HTTPS；真实客户端 IP 的限流和审计由公司网关负责。宿主发布端口
  默认只绑定 `127.0.0.1`，跨机接入时仅绑定明确私网 IP 并用防火墙只允许公司网关，
  禁止 `0.0.0.0`；
- Alembic 迁移是发布前单独动作，Compose 会显式注入迁移模式和预期数据库名，不能
  由 API 副本启动时执行。

仓库内 `teacher/` 教师端与根目录运营端同机部署时，使用
[联合部署说明](deploy/combined/README.md) 和
[联合 Compose](deploy/combined/docker-compose.yml)。两端使用不同域名、独立容器与
独立受限数据库角色，只共享同一个逻辑 PostgreSQL 数据库；宿主机只暴露统一 Edge。
联合部署门禁要求先按 `public 46 → teacher 0028 → public 50 → teacher 0032 → public 54 → teacher 0037 → public 55 → release public 56 → teacher 0038 → release public 57 → teacher 0040 → teacher 0041 → public 59 → public 60 → public 61 → public 62` 完成跨 Schema 迁移、隐私加固、文案更新与 DTS 领取索引升级，最终到达
public `20260818_62_dts_claim_idx` 和教师端 `0041_crm_sso_hybrid`，并同时通过
固定提交源码中的精确 `G01–G09` 标题/分值预检和目标数据库契约探针。
其中 public 54 阶段包含 rev51 G01 TESOL-only，teacher 37 阶段包含 0033 G01 规则迁移，
public 55 收敛教师源字段，release public 56 / teacher 0038 追加个性化拍照，
release public 57 / teacher 0039–0040 发布 G02 原生文档，teacher 0041 增加 CRM SSO；
public 59 合并 release 内容链与 ACL/DTS 分支，将运行权限统一为最终表级 ACL，并用
Trigger/受限视图保留业务所有权；public 60 在其后强制国内学生 HMAC token 的数据库边界，public 61 只更新 G01、G08、Lesson Memo 和 Attendance 的经审核文案，public 62 并发建立 DTS 脏键状态部分索引。
教师端未到 0041、最终 canonical 账本不是精确 36 条、目录缺项或语义错误都会失败关闭；在 public 47 及之后的空库直接
回放 teacher 历史链同样会失败关闭。即使门禁通过，也不能把“已有 Compose”解释为
已完成生产切流。

## 开发验证

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/alembic check

cd ../frontend
npm test -- --run
npm run lint
npm run build
```

## 本地空库模式

本地空库只用于开发，不包含当前真实测试数据：

```bash
docker compose up -d postgres

cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export DATABASE_URL='postgresql+psycopg://tit_growth_app@127.0.0.1:5432/tit_growth'
export APP_ENV=local
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_database.py
.venv/bin/python scripts/seed_config_center.py
```

空库 Seed 只创建当前任务模板和默认配置，不创建教师、课程或业务输出。

## 权威文档

- [当前架构](docs/architecture.md)
- [数据与积分规则](docs/数据与积分规则.md)
- [数据库表结构](docs/数据库表结构.md)
- [教师端共享任务表契约](contracts/教师端共享任务表契约.md)
- [教师端积分与课程读取对照表](contracts/教师端积分与课程读取对照表.md)
- [课程级数据契约](contracts/TIT课程级数据与Mock字段契约.md)
- [配置中心运行契约](docs/config-center-contract.md)
- [认证与 RBAC](backend/README_AUTH.md)
- [数据库运行说明](backend/README_DATABASE.md)
