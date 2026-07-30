# PostgreSQL 运行说明

运行时数据库固定为 PostgreSQL。SQLite 只允许由自动化测试显式注入，不能作为运营试跑事实源。仓库支持本机 Unix Socket 开发库 `tit_growth` 和公司共享测试库 `tit_growth_test` 两种非生产运行方式。当前持续运行的本地 Web App 指向 `tit_growth_test`；两库均已迁移到同一结构。这只代表开发/测试环境可用，不代表生产数据库已经上线。

教师工单使用教师端维护的共享事实表 `public.teacher_support_tickets`。TiDe 只读取该表，并通过
`public.append_teacher_support_ticket_operator_message(...)` 追加运营回复；不在本项目迁移中复制或管理该表。
字段、状态和上线权限边界见 `contracts/教师端共享工单表契约.md`。

## 当前数据构成与事实边界

- 当前共有 1065 位教师，全部来自 4 月海外新教师 30 天宽表并标记为 `MIXED`。运行库不保留显式 `MOCK` 教师、Mock assignment 或 Mock 调试输出。
- `data_import_batches` 保存教师快照、课程基线和投诉规则批次；`source_records` 无损保存外部原始行；`teacher_metric_snapshots` 与 `lesson_facts` 是可查询投影，不能取代原始证据。
- 五维当前总分保存在 `score_accounts`，维度内子项汇总保存在 `score_component_accounts`，逐课三个维度的计分事实保存在 `lesson_dimension_scores`。运营 API 和教师端只读视图都读取这些落库结果，不在页面打开时临时重算。
- 当前课程基线含 37,317 节课。个性化任务、课中质量提醒、运营 Case 和 `PENDING_DATA` 的数量由当前事实、任务状态和投诉映射实时投影；它们是本系统落库结果，不等于教师已收到或业务动作已完成。
- 当前宽表中的完课、Peak、好评、收藏、15 日复约、迟到、早退和 `peak_slot_cnt` 等按 `REAL / DERIVED_REAL` 追溯。外部宽表原始 `perfect_cnt` 只保存在 `raw_payload` 用于对账；当前类型化 `perfect_cnt` 按课程状态为 `end`、迟到为 0、早退为 0 的去重 `source_appoint_id` 实时汇总。15 日复约只保留事实、不计分；当前可靠性分读取 `perfect_cnt × 4 + peak_completed_cnt × 2`。课堂质量按课程事实计算：未开摄像头、CPU 占用过高、网络延迟过高三个字段都明确为 0 的课程，每节加 2 分；任一字段为空则该课不加分并标记 `SOURCE_MISSING`。
- 固定成长任务分实时读取共享 `task_assignments`：G01–G09 中每个合法 `COMPLETED` 按 assignment 固定引用的 `task_templates.payload.score_value` 累加。assignment 缺失或模板引用不合法时按证据不完整失败关闭，不使用教师快照中的默认成长任务分。
- L0 投诉次数由 `lesson_facts.complaint_level_rank = 0` 聚合；存在投诉但级别映射缺失时不能按 0 次处理。`severe_redline_event` 只保留为 v2–v4 历史兼容字段，不参与当前出营判断。
- `task_templates` 当前发布 9 个固定成长模板（G01–G09）和 5 个个性化改善模板；另有 1 个隐藏的退役固定模板 `G00` 只用于历史审计。新教师首次写入时由本系统幂等初始化 9 条 `ASSIGNED` 固定任务；少于 9 条记为内部 `TASK_BASELINE_INCOMPLETE`，不是外部 `SOURCE_MISSING`。共享 `task_assignments`、受限教师端角色、列权限、状态机、终态保护、乐观锁和统一审计已迁入公司测试库。
- 当前出营要求 G01–G09 全部完成、L0 投诉为 0、raw 总分不低于 100；满足三项后立即投影为已出营。金牌要求继承出营资格、raw 总分不低于 200、迟到不超过 1 次、早退 0 次、缺席 0 次。数据库不承担任何 72 小时等待结算状态。
- 源宽表没有可信教师 IANA 时区。类型化列中的 `UTC` 仅为数据库内部非空占位；人工或自动任务签发都不能使用该占位，缺少可信时区时必须 fail-closed。

当前测试环境：

- `tit_growth_test`：本项目隔离数据库，不使用默认 `postgres` 库承载业务表；
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

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_database.py
.venv/bin/python scripts/seed_config_center.py
```

- Alembic 是唯一建表和变更入口；API 启动不会自动 `create_all`。
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
- `seed_database.py` 只幂等补齐 14 个当前任务模板，不创建教师或任何运行时业务事实，也不删除导入批次、原始行、指标快照、课程投影和触发结果。G01–G09 assignment 由教师写入流程初始化；初始化不创建通知、提醒或投递意图。隔离测试中的 Mock fixture 不进入运营运行库。
- `seed_config_center.py` 只创建本地默认配置版本；空库读取不会由 API 隐式补配置。
- 两个 Seed 脚本都要求 `APP_ENV` 明确为 `local / dev / development / test`，否则拒绝执行。
- 运营账号由 `scripts/bootstrap_operator.py` 单独创建，数据库 Seed 不创建默认账号或密码。

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

共享任务完成后由独立结算进程消费 Outbox，写入成长任务积分，并同步当前教师快照和教师投影的未封顶总分、封顶展示分：

```bash
export APP_ENV=local
.venv/bin/python scripts/settle_shared_task_scores.py --watch --interval-seconds 3
```

课堂质量当前没有加分项。旧的课堂质量重算脚本只用于历史快照回查，不得在当前
v1 运行库执行。修改积分规则必须通过配置中心创建、校验和双人发布；发布事务会
全量重算当前教师投影，任一教师失败则整笔回滚。

首次迁移新增积分读取结构后，先预演再回填全部当前教师：

```bash
.venv/bin/python scripts/rebuild_score_read_model.py --test-database
.venv/bin/python scripts/rebuild_score_read_model.py --test-database --apply
```

日常无需运行此脚本：教师数据导入、课程数据导入、固定任务完成结算和积分版本发布都会自动更新对应投影。

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

- 任务模板、配置发布、运营 Case 处置与输出重试均在 PostgreSQL 事务内更新；
- 输出重试对输出行和关联通知行加锁，并在同一事务写审计和 Outbox；
- 经营总览、教师列表/详情、输出和审计均为数据库查询，服务启动不预加载全量业务状态；
- 共享测试 PostgreSQL 已验证两个并行 Worker 同时重试同一输出时只接受一次。

`G01`–`G09` 积分结算 Worker 仍保持单实例消费，并使用数据库行锁与幂等键防止重复结分。
站内通知、提醒、审批和外部动作的真实投递 Worker 尚未接入；它们没有完成前，输出记录只能
表示投递意图。

生产镜像和双 API Worker 编排示例见根目录 `docker-compose.production.yml`。公司生产环境仍需
完成备份恢复、迁移回滚、滚动发布、监控告警和真实负载验证。

教师端与运营端同机部署的 Host 分流、连接预算、迁移顺序和契约探针见
`deploy/combined/README.md`。两个应用必须连接同一个逻辑数据库，但 `public` 共享事实与
`tide` 教师执行事实的迁移所有权不能混用；当前教师端 `version2` 的旧任务编码修正前，
联合部署只是一套失败关闭的发布骨架。

此外，测试数据库持久化不等于生产接入完成：外部教师日更接口仍需接入“教师写入即初始化 G01–G09”的入口，教师端生产服务尚未完成受限角色连接、只更新已有任务状态和并发验收；站内通知、提醒、审批和外部动作仍缺真实 Worker。正式 Lesson 增量事实、可信教师时区、传输安全、备份恢复、部署、监控、权限治理和数据保留门禁全部通过前，只能称为公司测试环境内部试跑。

历史 `v2`–`v10` 快照仍可回读和审计，但旧复约积分、课堂质量分、严重红线字段和旧资格门槛不参与当前唯一 `v1` 口径的运行时投影。
