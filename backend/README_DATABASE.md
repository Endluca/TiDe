# PostgreSQL 运行说明

运行时数据库固定为 PostgreSQL。SQLite 只允许由自动化测试显式注入，不能作为运营试跑事实源。仓库支持本机 Unix Socket 开发库 `tit_growth` 和公司测试实例中的隔离数据库。旧库 `tit_growth_test` 保持在 revision 38；代码 head 为 public `20260807_49_unused_columns`、teacher `0030_remove_unused_columns_and_orphan_function`。`tit_growth_test_v2` 的 public 已到 rev49，但 tide 仍是合并前旧编号的 `0029_remove_unused_columns_and_orphan_function`，尚未包含 release 新增的 0027 本地 Quiz 清理；部署本分支前必须受控重建或完成账本与实存结构对账。已有测试只证明旧链的结构和源数据计算链已落地，不代表 canonical 0030、外部监控服务或生产已经上线。

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
- `task_templates` 当前发布 9 个固定成长模板（G01–G09）和 5 个个性化改善模板；另有 1 个隐藏的退役固定模板 `G00` 只用于历史审计。新教师首次写入时由本系统幂等初始化 9 条 `ASSIGNED` 固定任务；少于 9 条记为内部 `TASK_BASELINE_INCOMPLETE`，不是外部 `SOURCE_MISSING`。共享 `task_assignments`、受限教师端角色、列权限、状态机、终态保护、乐观锁和统一审计已迁入公司测试库。
- 当前出营要求 G01–G09 全部完成、L0 投诉为 0、raw 总分不低于 100；满足三项后立即投影为已出营。金牌要求继承出营资格、raw 总分不低于 200、迟到不超过 1 次、早退 0 次、缺席 0 次。数据库不承担任何 72 小时等待结算状态。
- 源宽表没有可信教师 IANA 时区。类型化列中的 `UTC` 仅为数据库内部非空占位；人工或自动任务签发都不能使用该占位，缺少可信时区时必须 fail-closed。

当前测试环境：

- `tit_growth_test`：保留的旧测试库，不执行本轮迁移或清理；
- `tit_growth_test_v2`：本轮新建的隔离测试库，`public` head 为
  `20260807_49_unused_columns`，`tide` 迁移账本 head 为
  合并前旧编号的 `0029_remove_unused_columns_and_orphan_function`；部署当前代码前需受控重建或对账；
- `tit_growth_app`：Web App 受限运行角色，无超级用户、建库、建角色和 Schema DDL 权限；
- `tit_teacher_crud`：教师端后端预留受限角色，只能按共享任务契约读取任务并更新已有任务的状态字段，不能创建或删除 assignment；
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

如果该库同时承载 teacher 的 `tide` Schema，首次初始化不能直接把 public 升到 head：必须按
public 46 → teacher 0028 → public 49 → teacher 0030 分阶段执行，详见
[`deploy/combined/README.md`](../deploy/combined/README.md)。只有不初始化 teacher Schema 的
独立 public 数据库才可直接执行以下 `upgrade head`。

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
`tit_growth_test_v2`，并创建无登录权限组 `tit_source_monitor`；实际监控服务账号后续加入该组。
revision 39 发现 `tit_growth_app` 或 `tit_source_monitor` 缺失、或 monitor 组可直接登录时会
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
- `20260807_46_teacher_g01_source` 在教师源表末尾追加两个可空 G01 状态字段，
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
  `sslmode=verify-full` 的固定非超级账号 `tit_growth_migrator`；连接角色或实际数据库
  不一致时会在 DDL 前停止。运行环境文件与迁移环境文件不得复用。
- `20260729_37_read_perf` 为结构化审计搜索创建 `pg_trgm` 扩展和 GIN 索引。迁移角色必须具备一次性 `CREATE EXTENSION` 权限，或由 DBA 在升级前执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm`；受限运行角色 `tit_growth_app` 不需要也不应获得该权限。
- `20260729_37_read_perf` 还会把旧的无真实消费方重试事件标记为 `PARKED`，并在
  payload 中保存迁移前状态和错误摘要；隔离回退验证可精确恢复。生产发布仍应先备份并
  统计受影响事件，不能把 downgrade 当作数据回滚方案。
- `20260729_38_catalog_scores` 把当前 G01–G09 的模板分值对齐为
  `3 / 2 / 2 / 3 / 3 / 4 / 3 / 5 / 5`。只有尚无固定任务积分流水、非零任务账户或
  非零任务分项时才自动修正；已有结分事实的库会整笔拒绝迁移，必须走受治理的积分规则
  发布与同事务全量重算，不能静默改写历史。
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

生产运行时必须为该进程配置一个独立 LOGIN 账号并使其仅继承 NOLOGIN 权限组
`tit_source_worker`；不能与 Web API 或外部源数据监控服务共用写账号。固定 LOGIN 名为
`tit_source_worker_runtime`，DBA 还需对目标数据库显式授予 `CONNECT`。生产环境从
[`backend/.env.source-worker.production.example`](.env.source-worker.production.example)
复制字段骨架到 Git 外的受保护文件；URL 必须使用已验证 TLS，真实密码由密钥管理注入。
进程健康同时要求本地 heartbeat 和最近一次数据库身份/选主探测 readiness 均未过期。

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
