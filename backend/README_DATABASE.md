# PostgreSQL 运行说明

运行时数据库固定为 PostgreSQL。SQLite 只允许由自动化测试显式注入，不能作为运营试跑事实源。仓库支持本机 Unix Socket 开发库 `tit_growth` 和公司测试实例中的隔离数据库。旧库 `tit_growth_test` 保持在 revision 38；代码 head 为 public `20260818_62_dts_claim_idx`、teacher `0041_crm_sso_hybrid`，teacher 为精确 36 条 canonical 账本。rev51/0033 将 G01 收窄为 TESOL-only，rev54/0037 将 G04 收窄为照片审核与课件准备两模块，rev55 将教师源表收敛为确认的 55 列，release rev56/0038 追加个性化环境拍照，release rev57/0039/0040 发布 G02 原生政策文档与阅读状态，0041 新增 CRM SSO 混合认证结构；public 59 汇合 release 内容链与 rev56–58 ACL/DTS 分支，public 60 在其后增加国内学生标识的数据库 fail-closed 边界，public 61 只更新经审核的教师文案，public 62 并发建立匹配 `PENDING` / 到期 `RETRY` 领取顺序的部分索引，最终权限以 `docs/数据库角色与权限最终版.md` 为准。业务字段所有权、状态机、不可逆事实和幂等账本继续由 Trigger/约束保护。公司 TEST 库 `tit_growth_test_v2` 上次已验证到 public `20260812_56_lean_roles`、teacher `0037_g04_remove_device_check`（精确 32 条 canonical 账本），不等于已应用 public 62 / teacher 0041；上线前仍须执行剩余迁移并以真实运行角色复验。重建前旧库封存为 `tit_growth_test_v2_pre0030_20260810`。这不代表外部日更、业务验收或生产已经上线。

教师工单使用教师端维护的共享事实表 `public.teacher_support_tickets`。TiDe 只读取该表，并通过
`public.append_teacher_support_ticket_operator_message(...)` 追加运营回复；不在本项目迁移中复制或管理该表。
字段、状态和上线权限边界见 `contracts/教师端共享工单表契约.md`。

## 当前数据构成与事实边界

旧库 `tit_growth_test` 的 1065 位教师与 37,317 节课程仅是历史试跑数据，不迁入当前库。
`tit_growth_test_v2` 当前只有显式 Seed 的 2 位虚构教师和 4 节虚构课程；6 条源变更事件已
全部成功消费，派生出 18 条 `ASSIGNED` 固定任务、1 条个性化任务、4 条
`lesson_score_results` 和 2 条资格状态。

- `teacher_source_wide` 与 `lesson_source_wide` 是当前唯一外部源数据入口。
  `teacher_metric_snapshots / lesson_facts / lesson_dimension_scores` 已物理删除；
  `complaint_rule_imports` 只保存投诉规则文件身份、文件名、43 行无损原文和导入时间，
  不参与宽表同步或计分。
- 五维当前总分保存在 `score_accounts`，维度内子项汇总保存在 `score_component_accounts`，
  一课一行的三个课程维度结果保存在 `lesson_score_results`。前两表分别以
  `teacher_id + dimension`、`teacher_id + component_code` 为复合主键，已删除代理 ID、营期、
  来源批次、最低分和权重等冗余列。运营 API 和教师端只读视图读取这些落库结果，不在页面打开时临时重算。
- 个性化任务、课中质量提醒、运营 Case 和 `PENDING_DATA` 由当前源事实、任务状态和投诉映射投影；它们是本系统落库结果，不等于教师已收到或业务动作已完成。
- 可靠性中的完美完课数按课程状态为 `end`、迟到为 0、早退为 0 的去重
  `source_appoint_id` 实时汇总；教师宽表 `perfect_cnt` 只用于对账。15 日复约只保留事实、不计分。课堂质量按逐课三项硬件异常字段计算，任一字段为空则该课不加分并标记 `SOURCE_MISSING`。
- 固定成长任务分实时读取共享 `task_assignments`：G01–G09 中每个合法 `COMPLETED` 按 assignment 固定引用的 `task_templates.payload.score_value` 累加。assignment 缺失或模板引用不合法时按证据不完整失败关闭，不使用教师快照中的默认成长任务分。
- L0 投诉次数由 `lesson_source_wide` 的三级投诉分类与当前 `complaint_category_rules` 精确匹配后聚合；存在投诉但级别映射缺失时不能按 0 次处理。`severe_redline_event` 只保留为 v2–v4 历史兼容字段，不参与当前出营判断。
- `task_templates` 当前发布 9 个固定成长模板（G01–G09）和 5 个个性化改善模板；另有 1 个隐藏的退役固定模板 `G00` 只用于历史审计。新教师首次写入时由本系统幂等初始化 9 条 `ASSIGNED` 固定任务；少于 9 条记为内部 `TASK_BASELINE_INCOMPLETE`，不是外部 `SOURCE_MISSING`。共享 `task_assignments`、最终表级 ACL、状态机、终态保护、乐观锁和统一审计已在代码中实现；业务写边界由 Trigger/约束保护。
- 当前出营要求 G01–G09 全部完成、L0 投诉为 0、raw 总分不低于 100；满足三项后立即投影为已出营。金牌要求继承出营资格、raw 总分不低于 200、迟到不超过 1 次、早退 0 次、缺席 0 次。数据库不承担任何 72 小时等待结算状态。
- 源宽表没有可信教师 IANA 时区。类型化列中的 `UTC` 仅为数据库内部非空占位；人工或自动任务签发都不能使用该占位，缺少可信时区时必须 fail-closed。

当前测试环境：

- `tit_growth_test`：保留的旧测试库，不执行本轮迁移或清理；
- `tit_growth_test_v2`：本轮受控重建的隔离测试库，上次已验证的 `public` head 为
  `20260812_56_lean_roles`，`tide` 为精确 32 条 canonical 账本且 head 为
  `0037_g04_remove_device_check`；重建前旧库以仅 DBA 可连接的
  `tit_growth_test_v2_pre0030_20260810` 保留为回滚点；
- `tit_growth_app`：Web App 受限运行角色，无超级用户、建库、建角色和 Schema DDL 权限；
- `tit_teacher_crud`：教师端后端运行角色按最终文档获得表级权限；assignment 虽有表级 CRUD，创建、删除和越权字段修改仍由数据库 Trigger 拒绝；
- 密码只存入本机 macOS 钥匙串，服务启动时读取，不写入仓库、环境文件或日志；
- 测试实例不支持 SSL，只允许在受控测试网络中使用。

未显式设置 `DATABASE_URL` 时，后端默认使用以下本机 Unix Socket 开发库：

```text
postgresql+psycopg://tit_growth_app@/tit_growth?host=/tmp
```

使用本机 `docker compose` 时改用：

```bash
export DATABASE_URL='postgresql+psycopg://tit_growth_app@127.0.0.1:5432/tit_growth'
```

容器的 `trust` 认证和 `127.0.0.1` 端口绑定只用于个人本机试跑，不能复制到共享或生产环境。

## 初始化空库

如果该库同时承载 teacher 的 `tide` Schema，首次初始化不能直接把 public 升到 head：必须先按
`public 46 → teacher 0028 → public 50 → teacher 0032 → public 54 → teacher 0037 → public 55 → release public 56 → teacher 0038 → release public 57 → teacher 0040 → teacher 0041`
完成跨 Schema 的 release 内容链，再依次合并 ACL/DTS 分支到 public 59、应用隐私边界到 public 60，详见
[`deploy/combined/README.md`](../deploy/combined/README.md)。只有不初始化 teacher Schema 的
独立 public 数据库才可直接执行以下 `upgrade head`。

已批准公司 TEST 库从 public 50 / teacher 0032 继续时，使用仓库根目录下的总控脚本。
它默认只读：先硬校验固定主机、库名、owner、配置文件权限、public revision 链和 teacher
canonical 账本 checksum，再输出剩余切换点。只有显式确认备份和维护窗口后才会逐段写入，
每段写完都会重新读取双账本；失败后只能从脚本列出的中间切换点续跑。

```bash
cp backend/company-test-migration.env.example /Git工作区外/company-test-migration.env
chmod 600 /Git工作区外/company-test-migration.env
# 从批准的密钥系统填入密码；先执行只读检查。
backend/.venv/bin/python backend/scripts/upgrade_company_test_database.py \
  /Git工作区外/company-test-migration.env

# 仅在备份可恢复、API/Worker/其他迁移器已停止写入后执行。
backend/.venv/bin/python backend/scripts/upgrade_company_test_database.py \
  /Git工作区外/company-test-migration.env \
  --apply --backup-confirmed --maintenance-window-confirmed
```

总控脚本不会运行 teacher 初始化器、Mock/内容 Seed、发布 Gaea 或重启服务。到达 public 57 /
teacher 0041 后，仍需使用包含 `tit_teacher_crud` 凭据的另一份 Git 外配置运行
`teacher/backend/database/scripts/apply-company-test.sh`，再进行应用发布与端到端验收。

```bash
cd backend
# DATABASE_URL 必须使用单独的迁移/owner 角色，不能使用 tit_growth_app。
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_database.py
.venv/bin/python scripts/seed_config_center.py
```

数据库重构测试目标使用新库 `tit_growth_test_v2`，不在现有
`tit_growth_test` 上原地清理。迁移脚本要求连接串库名和实时
`current_database()` 都等于同一个显式值：

迁移脚本不执行 `CREATE DATABASE`。首次运行前由数据库管理员在已确认的实例新建空库
`tit_growth_test_v2`，并创建受限 LOGIN `tit_dts_ingest_runtime`。revision 39 发现
`tit_growth_app` 或 `tit_dts_ingest_runtime` 缺失，或者 DTS 账号具有管理权限/角色继承时会
整笔拒绝迁移；它不创建账号、不设置密码。当前测试库已完成该步骤，以下命令保留为可复现入口。

```bash
export TIT_DATABASE_OWNER_ROLE='<database owner role>'
export TIT_DATABASE_OWNER_KEYCHAIN_SERVICE='<Keychain service>'
export TIT_DATABASE_OWNER_HOST='<test instance host>'
export TIT_DATABASE_OWNER_PORT='5432'
export TIT_DATABASE_OWNER_DATABASE='tit_growth_test_v2'
export TIT_DATABASE_OWNER_SSLMODE='disable'
export TIT_MIGRATION_EXPECTED_DATABASE='tit_growth_test_v2'
export APP_ENV='test'
.venv/bin/python scripts/migrate_test_database.py
.venv/bin/python scripts/seed_source_test_data.py --test-database
.venv/bin/python scripts/seed_source_test_data.py --test-database --apply
```

最后两条分别是预演和显式提交。`--apply` 必须与 `--test-database` 同时使用，且脚本硬限已批准的测试主机、`tit_growth_test_v2` 和 owner 角色。测试 Seed 显式写入两张新源表中的 2 位虚构教师与
4 节虚构课程；源表 Trigger 会在同一事务产生 6 条 `PENDING` Outbox 事件。SourceWide
Worker 在源事务提交后消费事件，并在独立事务中幂等更新派生结果；未处理事件会阻止
revision 39 降级。Seed 不清理其他数据，不写旧导入表。密码只从
macOS Keychain 读取，不能写进命令、仓库或环境文件。
迁移和受保护 Seed 也会在联网前拒绝 libpq 的 `PGHOST*`、`PGOPTIONS`、
`PGSERVICE*` 等环境覆盖，避免实际目标或 Trigger 会话语义被外部环境改写。

- Alembic 是唯一建表和变更入口；API 启动不会自动 `create_all`。
- `20260806_39_source_wide` 新增原始 61/23 列的两张源表和字段差异 Outbox；
  `scripts/run_source_wide_worker.py` 消费这些事件并更新新派生结果。源表提交与派生更新是两个
  事务，正常轮询间隔为 3 秒，因此是短暂最终一致，不是同步触发器计算。
- `20260806_40_runtime_acl` 为运营运行角色逐表、逐操作授权；源表只读、任务实例只读、
  运营账号只允许更新 `password_hash`，禁止 Schema `CREATE`、角色继承和 `public` 对象所有权。
  迁移、Seed 和首次运营账号创建继续使用单独管理凭据，不能给运行角色临时扩权。
- `20260806_41_runtime_acl_columns` 只补充登录时密码哈希升级同时需要的 `updated_at`
  列更新权；用户名、启停状态、角色授权仍不可由运行角色修改。
- `20260806_42_effective_acl` 清理历史视图和 `PUBLIC` 继承授权，按最终有效权限
  而不是单次 `GRANT/REVOKE` 语句验收；教师端角色的直接授权不受影响。
- `20260806_43_source_results` 新增一课一行的 `lesson_score_results` 和保存当前门槛、
  不可逆获得事实的 `teacher_qualifications`，并将个性化触发课程 FK 指向课程源表。
- `20260806_44_source_reads` 将两个教师端积分视图切到源表与新结果表。
- `20260807_46_teacher_g01_source` 在教师源表末尾追加两个可空教师资料状态字段；当前 G01 只消费 TESOL，
  并把教师端角色收紧为只读教师键和这两个状态字段；旧快照不再是 G01 读取入口。
- `20260807_47_legacy_drop` 要求 teacher 0028 已先退役旧分析视图，并在三张旧投影为空且
  不存在外部视图、外键、继承/分区或发布依赖时，无 `CASCADE` 删除它们。
- `20260807_48_schema_cleanup` 将通用投诉批次与逐行来源合并为四字段
  `complaint_rule_imports`，原位保留并瘦身 `score_accounts`、`score_component_accounts`，
  删除无值的 `teachers.source_batch_id` 等冗余列，并在空表和依赖门禁通过后无 `CASCADE`
  删除 `source_records`、`agent_decisions`、`provider_calls`、`outbound_outputs`。迁移会先验证
  被删积分列确实等于自然键、教师营期或零值，来源批次列确实为空；出现非冗余值时整笔停止。
  rev48 downgrade 不伪造已删除的投诉导入元数据：`complaint_rule_imports` 非空时会明确拒绝
  降级，必须先导出并按受控方案处理。
- teacher `0029_remove_unused_tide_objects` 以相同的空表、G00 退役和依赖门禁删除 6 张无消费者
  表及 5 个被当前分析实现替代的视图；当前 G04 图片审核继续使用提交、文件和图片审核表。
- `20260807_49_unused_columns` 在逐行可还原性门禁后删除
  `complaint_category_rules.learning_title / learning_url` 和未被独立维护的
  `operator_sessions.last_seen_at`；前两列的完整值仍保留在 `complaint_rule_imports.raw_rows`。
- teacher `0030_remove_unused_columns_and_orphan_function` 在确认所有文件均为
  `PRIVATE`、无外部列依赖且函数无消费者后，无 `CASCADE` 删除
  `tide.file_objects.visibility` 和孤儿 `tide.enforce_outbox_target()`。
- 国内/海外两个业务库到宽表的字段查询 SQL 与影响映射尚未提供；
  Otter、MQ 消费和宽表写入逻辑仍属待实现的上游边界。
- `20260806_45_source_runtime_acl` 只授予运行服务重算新结果所需的读取、插入和列级更新权；
  运行服务仍不能写源表、改结果主键或删除资格。
- 生产 Alembic 必须显式设置 `TIT_MIGRATION_MODE=true` 和目标数据库名，使用
  `sslmode=verify-full` 的现有管理账号 `tide_sys_admin`；连接角色或实际数据库
  不一致时会在 DDL 前停止。当前固定 `tide_system_test` 专线 PRE 可由迁移与只读契约探针各自的
  管理连接串使用 `sslmode=disable`；迁移入口按 URL 白名单锁定固定端点、库名和管理角色，契约
  探针还必须显式设置 `TIDE_CONTRACT_PROBE_REQUIRE_SSL=false`。该探针变量只是选择已锁定的 PRE
  分支，不是放宽任意目标的全局开关。专线不是 TLS，正式环境仍必须 `verify-full`。运行环境文件
  与迁移环境文件不得复用。
- `20260729_37_read_perf` 为结构化审计搜索创建 `pg_trgm` 扩展和 GIN 索引。迁移角色必须具备一次性 `CREATE EXTENSION` 权限，或由 DBA 在升级前执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm`；受限运行角色 `tit_growth_app` 不需要也不应获得该权限。
- `20260729_37_read_perf` 还会把旧的无真实消费方重试事件标记为 `PARKED`，并在
  payload 中保存迁移前状态和错误摘要；隔离回退验证可精确恢复。生产发布仍应先备份并
  统计受影响事件，不能把 downgrade 当作数据回滚方案。
- `20260729_38_catalog_scores` 把当前 G01–G09 的模板分值对齐为
  `3 / 2 / 2 / 3 / 3 / 4 / 3 / 5 / 5`。只有尚无固定任务积分流水、非零任务账户或
  非零任务分项时才自动修正；已有结分事实的库会整笔拒绝迁移，必须走受治理的积分规则
  发布与同事务全量重算，不能静默改写历史。
- `20260810_50_g04_sections` 以 `20260807_49_unused_columns` 为直接前驱，仅更新
  重排后承载当前 G04 的稳定物理行
  `G02:v1`（其 `template_id = G04`）的 How、完成标准与
  benefit 文案：备课须知、设备网络基础检测和授课环境照片审核为三个任意
  顺序、独立保存进度的模块，三项全部通过才完成 G04。该迁移不修改编码、
  分值、assignment 或任务状态，发现旧文案漂移时失败关闭。
- `20260811_51_g01_tesol_only` 以 rev50 为直接前驱，仅将稳定 `G01:v1`
  的上游资料状态收窄为 TESOL，保留模板身份、分值、assignment 和原有完成事实；
  同时撤销 `tit_teacher_crud` 对 `is_self_introduce` 的读取权，物理列仍保留给源数据 owner
  和运营投影。发现旧文案或稳定身份漂移时迁移失败关闭。
- teacher `0033_g01_tesol_only` 原位更新 G01 外部状态校验规则为 TESOL-only，
  保留 execution、step/rule ID、assignment 与教师进度；未知规则结构失败关闭。
- `20260811_54_g04_remove_device_check` 继续原位更新同一稳定 G04 模板的标题与四段文案，
  只保留授课环境照片 AI 审核和课件准备确认。teacher `0037_g04_remove_device_check`
  从当前 execution 删除设备步骤定义并把完成规则收窄为上述两项；既有设备检测进度仍保留
  为历史审计事实，不修改任何 assignment、终态或分值。
- `20260811_55_source_wide_v12` 以无 `CASCADE` 删列将教师源表从 63 列收敛为
  53 个确认映射字段加 2 个教师资料状态字段；课程源表继续严格保持 23 列。存在数据时
  downgrade 会失败关闭，避免伪造已删除值。
- `20260812_56_lean_roles` 将 API、积分结算和 SourceWide 计算统一到
  `tit_growth_app`，将 DTS 源事实写入限定为 `tit_dts_ingest_runtime`，并撤销旧
  SourceWide group-role 对相关表的授权；教师端继续独立使用 `tit_teacher_crud`。
- `20260812_57_dts_state` 新增 DTS 事件账本、字段白名单当前态、反向依赖 GIN 索引、脏键和数据库位点；`run_dts_ingest.py` 在接入事务后调用投影器重算两张源宽表。
- 海外 DTS 进程必须运行在新加坡，国内 DTS 进程必须运行在中国大陆；运行区域声明与来源区域
  不匹配时失败关闭。国内进程在构造任何发往海外 PostgreSQL 的 SQL 参数前，将原始学生 ID
  替换为 `dom:v1:<HMAC-SHA256>`，密钥由 CSPRNG 生成 32 bytes、精确编码为 64 位小写 hex
  且只注入国内项目；海外进程和海外数据库
  不得持有该密钥或原始国内学生 ID。该稳定 token 仍是受限伪名数据。若安全边界不允许稳定 token
  跨境，必须改为国内状态库聚合后只发送不可回链指标。首次启动会在受限状态行登记 HMAC 密钥
  fingerprint；后续不匹配即退出，禁止无迁移直接轮换密钥。
- 国内跨境连接默认及正式环境要求 `TIT_DTS_INGEST_DB_SSLMODE=verify-full` 与
  `TIT_DTS_ALLOW_INSECURE_DB=false`，并固定关闭投影；海外进程是双 checkpoint 激活后的唯一投影
  owner。当前固定 `tide_system_test` PRE 专线端点允许国内、海外项目同时设置 `disable/true`，任何
  范围漂移失败关闭。专线不等于加密；目标数据库启用 TLS 后必须恢复 `verify-full/false`。
- DTS 持久化进程每次启动先只读校验目标库身份、Catalog、ACL 和 checkpoint，再从当前 Pod
  完成 bootstrap DNS 解析，对解析结果做 5 秒共享连接预算、无凭据且不收发应用数据的 TCP 探针，最后以相同运行密钥
  校验 Kafka SASL/topic/partition/初始位点；全部通过后才写 readiness。TCP 四层失败使用
  `DTS_BROKER_TCP_*` 稳定错误码；TCP 已通后的 Kafka 请求超时使用
  `DTS_BROKER_KAFKA_REQUEST_TIMEOUT`。该启动探针不会创建
  `dts_ingest_events/source_rows/dirty_keys/checkpoints` 记录，也不会消费消息或提交 Kafka offset；
  国内进程只在探针成功后、ready 前幂等登记一条不含密钥或学生标识的 HMAC fingerprint 契约行；
  除该固定契约行外，四张状态表的首次业务变化只能来自正式消息事务。这些检查在每次容器进程启动/重启时执行，不属于
  镜像构建或周期 healthcheck。
- `20260812_58_table_acl` 撤销运行账号的显式列级 ACL，改用表级权限；任务、Outbox、
  账号、通知、工单和逐课结果的字段边界由 Trigger 强制，教师 G01 只读两列受限视图。
- `20260812_59_simple_acl` 合并 release 内容分支与 ACL 分支，落实最终三列表中的表级权限；DTS 状态表虽授予 CRUD，物理删除、事件账本改写和位点回退仍由 Trigger 拒绝。
- `20260813_60_dom_privacy` 在 rev59 之后校验既有 DTS/课程状态，并用数据库 Trigger 拒绝任何国内原始学生 ID 或非法 token 落入海外目标库。
- `20260814_61_teacher_copy` 仅原位更新 G01、G08、Lesson Memo 和 Attendance 的经审核教师文案，不改 assignment 和状态事实。
- `20260818_62_dts_claim_idx` 是当前 public head；它用 `CREATE INDEX CONCURRENTLY` 为 `PENDING` FIFO 和到期 `RETRY` 队列增加部分索引，不改业务事实。
- `seed_database.py` 只幂等补齐 14 个当前任务模板，不创建教师或任何运行时业务事实，也不修改投诉规则导入或触发结果。G01–G09 assignment 由教师写入流程初始化；初始化不创建通知、提醒或投递意图。隔离测试中的 Mock fixture 不进入运营运行库。
- `seed_config_center.py` 只创建本地默认配置版本；空库读取不会由 API 隐式补配置。
- 两个 Seed 脚本都要求 `APP_ENV` 明确为 `local / dev / development / test`，否则拒绝执行。
- 运营账号由 `scripts/bootstrap_operator.py` 使用迁移/管理角色单独创建，数据库 Seed 不创建
  默认账号或密码；该脚本不得复用 `tit_growth_app` 的运行连接。

## 启动与检查

```bash
export APP_ENV=local
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010
```

业务交接优先使用根目录的一键启动脚本：

```bash
./scripts/start.sh /安全路径/TiDe.env
```

如需通过 macOS 钥匙串单独启动后端，先配置非敏感连接元数据：

```bash
export TIT_TEST_DATABASE_ROLE='<受限角色>'
export TIT_TEST_DATABASE_KEYCHAIN_SERVICE='<Keychain service>'
export TIT_TEST_DATABASE_HOST='<host>'
export TIT_TEST_DATABASE_PORT='5432'
export TIT_TEST_DATABASE_NAME='<database>'
export TIT_TEST_DATABASE_SSLMODE='disable'
.venv/bin/python scripts/start_test_backend.py
```

也可以直接设置 `DATABASE_URL`。脚本不会打印数据库密码。

共享任务完成后由独立结算进程消费 Outbox，写入成长任务积分，并同步当前教师投影的未封顶总分、封顶展示分和资格；它只接受存在 `teacher_source_wide` 当前源行的教师：

```bash
export APP_ENV=local
.venv/bin/python scripts/settle_shared_task_scores.py --watch --interval-seconds 3
```

两张当前源表由另一个独立进程消费。日常更新采用字段级路由：完全无关的资料字段不会重算
积分；计分字段只重算涉及的教师，不全库扫描。课程聚合为保证确定性，会读取该受影响教师
的课程集合；积分规则发布才执行受治理的全量重算。

```bash
export APP_ENV=local
.venv/bin/python scripts/run_source_wide_worker.py --watch --interval-seconds 3
```

生产运行时该进程与运营 API、积分结算同属 TiDe 后端信任边界，共用受限 LOGIN
`tit_growth_app`；它不能与外部 DTS 入库账号或教师端账号共用。生产环境复用
[`backend/.env.production.example`](.env.production.example) 的受保护运行文件；URL 必须
使用已验证 TLS，真实密码由密钥管理注入。
进程健康同时要求本地 heartbeat 和最近一次数据库身份/选主探测 readiness 均未过期，并且
`source_wide.changed.v1` Outbox 中不存在 `DEAD_LETTER`，也不存在已发生失败
（`attempt_count > 0`）且在 `available_at` 到期后仍滞留超过 900 秒的 `PENDING`。阈值可由
`TIT_SOURCE_WORKER_MAX_PENDING_AGE_SECONDS` 调整；首次解暂停时未尝试的历史积压和正常的
未来退避事件不计为超龄，数据库查询失败时健康检查失败关闭。

课堂质量按逐课三项硬件异常字段均明确为 0 时每课加 2 分。旧的课堂质量重算脚本已退役，调用会在读取凭据或连接数据库前返回 `LEGACY_CLASS_QUALITY_RECALCULATION_RETIRED`。修改积分规则必须通过配置中心创建、校验和双人发布；发布事务会
全量重算当前教师投影，任一教师失败则整笔回滚。

首次迁移新增积分读取结构后，先预演再回填全部当前教师：

```bash
.venv/bin/python scripts/rebuild_score_read_model.py --test-database
.venv/bin/python scripts/rebuild_score_read_model.py --test-database --apply
```

旧教师 XLSX 导入已退役，调用会在读文件或连接数据库前返回
`LEGACY_TEACHER_METRIC_IMPORT_RETIRED`。课程文件导入器只作为离线兼容工具保留；当前
SourceWide Worker 不导入它。新宽表教师的固定任务结算只更新任务分、教师总分和资格，不扫描
课程；积分版本发布只重算 `SOURCE_WIDE_CURRENT` 教师，混入旧教师会整笔失败并回滚。

```bash
.venv/bin/alembic current
.venv/bin/alembic check
curl http://127.0.0.1:8010/api/health
curl http://127.0.0.1:8010/api/health/db
```

非生产健康检查应返回 PostgreSQL dialect，并暴露
`single_process_required: false`；生产健康检查只返回最小状态。

## 当前并发边界

运营 API 的当前公开路径已不再依赖启动时全量工作集：

- 任务模板、配置发布和运营 Case 处置均在 PostgreSQL 事务内更新；
- 经营总览、教师列表/详情、输出页和审计均为数据库查询，服务启动不预加载全量业务状态；
- 输出页直接读取 `task_assignments`、`notifications`、`ops_cases` 等权威事实，不再维护
  `outbound_outputs` 重复副本。

`G01`–`G09` 积分结算 Worker 可随应用多副本启动，但通过 PostgreSQL 执行权保持逻辑单活，
并使用数据库行锁与幂等键防止重复结分。
站内通知、提醒、审批和外部动作的真实投递 Worker 尚未接入；它们没有完成前，通知记录只能
表示投递意图。

生产镜像和双 API Worker 编排示例见根目录 `docker-compose.production.yml`。公司生产环境仍需
完成备份恢复、迁移回滚、滚动发布、监控告警和真实负载验证。

教师端与运营端同机部署的 Host 分流、连接预算、迁移顺序和契约探针见
`deploy/combined/README.md`。两个应用必须连接同一个逻辑数据库，但 `public` 共享事实与
`tide` 教师执行事实的迁移所有权不能混用；当前教师端 `version2` 的旧任务编码修正前，
联合部署只是一套失败关闭的发布骨架。

此外，测试数据库持久化不等于生产接入完成：外部教师日更接口仍需接入“教师写入即初始化 G01–G09”的入口，教师端生产服务尚未完成受限角色连接、只更新已有任务状态和并发验收；站内通知、提醒、审批和外部动作仍缺真实 Worker。正式 Lesson 增量事实、可信教师时区、传输安全、备份恢复、部署、监控、权限治理和数据保留门禁全部通过前，只能称为公司测试环境内部试跑。

历史 `v2`–`v10` 快照仍可回读和审计，但旧复约积分、课堂质量分、严重红线字段和旧资格门槛不参与当前唯一 `v1` 口径的运行时投影。
