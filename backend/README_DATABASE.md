# PostgreSQL 运行说明

运行时数据库固定为 PostgreSQL。SQLite 只允许由自动化测试显式注入，不能作为运营试跑事实源。当前本机 Web App 与积分结算 Worker 已连接公司测试 PostgreSQL 的独立数据库 `tit_growth_test`；这代表测试环境迁移完成，不代表生产数据库已经上线。

## 当前数据构成与事实边界

- 当前共有 1065 位教师，全部来自 4 月海外新教师 30 天宽表并标记为 `MIXED`。运行库不保留显式 `MOCK` 教师、Mock assignment 或 Mock 调试输出。
- `data_import_batches` 保存教师快照、课程基线和投诉规则批次；`source_records` 无损保存外部原始行；`teacher_metric_snapshots` 与 `lesson_facts` 是可查询投影，不能取代原始证据。
- 当前课程基线含 37,317 节课。当前规则投影为 829 个开放个性化任务、512 条课中质量提醒、0 个运营 Case 和 0 条 `PENDING_DATA`；其中 61 个任务来自“同一教师、同一差评标签至少命中 2 节差评课”。这些数量是本系统落库结果，不是送达回执。
- 当前宽表中的完课、Peak、好评、收藏、15 日复约、迟到、早退、`perfect_cnt` 和 `peak_slot_cnt` 等按 `REAL / DERIVED_REAL` 追溯。当前课堂质量分为 `perfect_cnt × 1.6`，不再读取 80% 课堂质量模拟值。
- 固定成长任务分实时读取共享 `task_assignments`：G01–G10 中每个合法 `COMPLETED` 按 assignment 固定引用的 `task_templates.payload.score_value` 累加。assignment 缺失或模板引用不合法时按证据不完整失败关闭，不使用教师快照中的默认成长任务分。
- L0 投诉次数由 `lesson_facts.complaint_level_rank = 0` 聚合；存在投诉但级别映射缺失时不能按 0 次处理。`severe_redline_event` 只保留为 v2–v4 历史兼容字段，不参与当前出营判断。
- `task_templates` 当前包含 10 个固定成长模板和 5 个个性化改善模板。新教师首次写入时由本系统幂等初始化 10 条 `ASSIGNED` 固定任务；少于 10 条记为内部 `TASK_BASELINE_INCOMPLETE`，不是外部 `SOURCE_MISSING`。共享 `task_assignments`、受限教师端角色、列权限、状态机、终态保护、乐观锁和统一审计已迁入公司测试库。
- 当前出营只要求 G01–G10 全部完成、L0 投诉为 0、raw 总分不低于 100；满足三项后立即投影为已出营。金牌只要求继承出营资格且 raw 总分不低于 200。数据库不承担任何 72 小时等待结算状态。
- 源宽表没有可信教师 IANA 时区。类型化列中的 `UTC` 仅为数据库内部非空占位；人工或自动任务签发都不能使用该占位，缺少可信时区时必须 fail-closed。

当前测试环境：

- `tit_growth_test`：本项目隔离数据库，不使用默认 `postgres` 库承载业务表；
- `tit_growth_app`：Web App 受限运行角色，无超级用户、建库、建角色和 Schema DDL 权限；
- `tit_teacher_crud`：教师端后端预留受限角色，只能按共享任务契约读取任务并更新已有任务的状态字段，不能创建或删除 assignment；
- 密码只存入本机 macOS 钥匙串，服务启动时读取，不写入仓库、环境文件或日志；
- 测试实例不支持 SSL，只允许在受控测试网络中使用。

以下本机 Unix Socket 连接仅作为独立开发库的可选方式，不是当前托管服务的活动连接：

```text
postgresql+psycopg://tit_growth_app@/tit_growth?host=/tmp
```

使用仓库 `docker compose` 时改用：

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
- `seed_database.py` 只幂等补齐 15 个当前任务模板，不创建教师或任何运行时业务事实，也不删除导入批次、原始行、指标快照、课程投影和触发结果。G01–G10 assignment 由教师写入流程初始化；初始化不创建通知、提醒或投递意图。隔离测试中的 Mock fixture 不进入运营运行库。
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

课堂质量规则首次切换到 `perfect_cnt × 1.6` 时，先预演、核对行数，再显式应用当前发布策略：

```bash
.venv/bin/python scripts/recalculate_class_quality_scores.py --test-database
.venv/bin/python scripts/recalculate_class_quality_scores.py --test-database --apply
```

默认命令只读计算并回滚，不落库；`--apply` 仅重算每位教师当前批次的 `teacher_metric_snapshots` 派生分与策略元数据、`teachers.payload` 中的当前指标/课堂质量维度/总分，以及 `score_accounts` 的 `CLASS_QUALITY` 账户。`data_import_batches`、快照 `raw_payload` 和历史非当前批次不改写。脚本读取当前 `PUBLISHED` 的 v5/v6 策略并可幂等重复执行。

```bash
.venv/bin/alembic current
.venv/bin/alembic check
curl http://127.0.0.1:8010/api/health
curl http://127.0.0.1:8010/api/health/db
```

健康检查应返回 PostgreSQL dialect，并暴露 `single_process_required: true`。

## 当前并发边界

PostgreSQL 已经保证单 API 进程重启后数据可恢复，但当前领域写路径仍基于单进程工作集再合并落库。进程内锁不能防止多个 API 进程持有不同快照后覆盖彼此更新。

因此当前：

- 只启动一个 API 进程，不使用 `--workers`；
- `G01`–`G10` 积分结算 Worker 已可运行，并使用数据库行锁与幂等键防止重复结分；
- 站内通知、提醒、审批和外部动作的真实投递 Worker 尚未接入；
- 生产级进程托管、监控告警、失败恢复和 Outbox 保留策略仍属于上线改造。

生产前必须把写命令改为事务型 Repository，在数据库事务中只更新本次涉及的聚合，并完成行锁或版本号冲突测试、多 Worker 回归、备份恢复和迁移回滚验证。

此外，测试数据库持久化不等于生产接入完成：外部教师日更接口仍需接入“教师写入即初始化 G01–G10”的入口，教师端生产服务尚未完成受限角色连接、只更新已有任务状态和并发验收；站内通知、提醒、审批和外部动作仍缺真实 Worker。正式 Lesson 增量事实、可信教师时区、传输安全、备份恢复、部署、监控、权限治理和数据保留门禁全部通过前，只能称为公司测试环境内部试跑。

历史 `v2`–`v5` 快照仍可回读和审计，但旧课堂质量达成率、严重红线字段、旧出营门槛和 v5 的额外金牌门槛不参与当前 `v6` 重算。
