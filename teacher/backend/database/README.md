# PostgreSQL 设计

## 定位

- 版本：PostgreSQL 16。本地库 `tide_dev`，默认端口 `55432`。
- 生产架构是两端共用一个 PostgreSQL；教师端自有表放在 `tide` Schema。
- 共享 `public.task_templates/task_assignments` 不属于教师端迁移。`fixtures/0001_shared_contract.sql` 只为本地和测试提供最小共享表契约。
- 结构真相源是 `migrations/`。Seed 全部使用 `.invalid`、`MOCK-*` 和 Mock 来源标记。

## 当前迁移

| 迁移 | 作用 |
|---|---|
| `0001–0006` | 历史基础结构，保留用于已有开发库升级和回滚 |
| `0007_shared_task_assignment_links` | 新增本地执行版本，将过程表关联到共享 assignment，新增系统消息并将埋点改名为 `app_events` |
| `0008_remove_legacy_task_exchange` | 删除本地任务副本、世文 assignment 副本、事件发布链、长期业务投影和 Support 表 |
| `0009_task_view_command` | 允许用幂等命令明确记录 `ASSIGNED → VIEWED` |
| `0010_current_task_execution` | 放开执行配置任务编码，兼容当前个性化任务编码 |
| `0011_system_notification_delivery` | 增加配置发布、个人通知快照、固定系统内操作、消息分页索引和外部事件幂等 |
| `0012_system_notification_publication_guards` | 锁定已发布／已撤销配置，禁止重开或修改收件人快照 |
| `0013_system_notification_owner_maintenance` | 应用角色继续禁止删除；数据库 Owner 可执行合规清理和隔离测试维护 |
| `0014_teacher_photo_processing` | 历史迁移：曾建立隐藏 G00 照片处理表；当前运行结构已由 0029 退役 |
| `0015_teacher_photo_filter_strength` | 历史迁移：曾扩展隐藏 G00 图片滤镜强度；当前运行结构已由 0029 退役 |
| `0016_database_quiz_banks` | 新增版本化数据库题库；题目和答案不再保存在前端或任务步骤 JSON |
| `0017_task_assignment_teacher_response` | 历史迁移：曾为拉黑任务增加教师事实说明字段 |
| `0018_remove_task_assignment_teacher_response` | 按最新运营确认流程删除共享 assignment 上的教师事实说明字段 |
| `0019_growth_stage_notification_state` | 保存教师端成长阶段通知的观察基线 |
| `0020_product_analytics` | 增强产品事件并建立只读分析视图 |
| `0021_teacher_support_tickets` | 建立双方共用工单表和原子消息函数 |
| `0022_performance_job_leases` | 建立当前后台调度租约；其中历史 G00 照片租约字段随后由 0029 随表退役 |
| `0023_teacher_support_operator_atomicity` | 运营回复与 `WAITING_TEACHER`、回复时间、48 小时窗口在同一行锁事务内提交 |
| `0024_support_ticket_cas_and_function_owner` | 拒绝 NULL expected row version 和 NULL message，并把全部工单 SECURITY DEFINER 函数固定给非登录最小权限 owner；该安全迁移 forward-only |
| `0025_fixed_task_semantic_alignment` | 按运营端稳定 `row_id` 将旧 G01–G10 执行语义原位对齐到当前 G01–G09，旧 G05 归档为 retired G00；保留 execution ID、共享模板关联和过程数据；新增通过稳定 assignment/template 解析当前编码的 analytics v2，raw code 仅作审计；该业务身份迁移 forward-only |
| `0026_kuozhi_course_syncs` | 保存阔知正式课程刷新快照、双重幂等回执和完成判定；达标时与共享 assignment 状态更新同事务提交，数据库只接受 `dataMode = REAL` |
| `0027_remove_local_quiz_runtime` | 删除站内题库、作答与考试步骤；所有考试统一以阔知 `percent=100` 判定 |
| `0028_retire_task_business_change_view` | 退役依赖旧教师历史快照的 `analytics_task_business_change_v1`；不删除 `public` 表、不使用 `CASCADE`，down 精确恢复原视图 |
| `0029_remove_unused_tide_objects` | 在空表、G00 路由和外部依赖门禁后，无 `CASCADE` 删除 6 张无消费者表及 5 个已被 v2 替代的分析视图；down 精确恢复结构和视图定义 |
| `0030_remove_unused_columns_and_orphan_function` | 锁定 `file_objects` 并确认全部对象均为私有、无外部列依赖后，删除恒定 `visibility` 字段；确认无消费者后删除 `enforce_outbox_target()` 孤儿函数；全程不使用 `CASCADE`，down 精确恢复字段、约束和原函数定义 |

`0008` 在删除前会阻断任何未映射的过程数据或非 Mock 个性化任务，不会静默丢弃真实数据。

## Seed 与脚本

| 文件 | 作用 |
|---|---|
| `seed/0000_mock_shared_catalog.sql` | 本地共享教师、当前 G01–G09、retired G00、G01 证据和外部消息 |
| `fixtures/0002_score_entry_contract.sql` | 仅本地升级共享积分流水测试契约，补齐课程结分字段；不得用于公司或生产库 |
| `fixtures/0003_course_score_snapshot_contract.sql` | 仅为本地当前视图 Mock 补齐世文源字段；公司库已由世文持有，不得用于公司或生产库 |
| `seed/0002_mock_shiwen_views.sql` | 教师资料、积分总览和逐课积分当前视图 Mock |
| `scripts/sync-current-task-catalog.ts` | 当前 G01–G09 与已确认个性化任务的唯一执行配置同步脚本；按稳定共享模板行更新，不重建 execution |
| `seed/0004_mock_faq_knowledge.sql` | 已确认规则的 FAQ Mock 知识 |
| `scripts/import-company-test-faq.sh` | 校验并将 124 条全量 Canonical FAQ 与语义匹配／回答 Prompt 版本导入公司测试库 |
| `scripts/apply.sh` | 本地幂等升级至 0030，并应用当前 Seed/本地权限 |
| `scripts/apply-company-test.sh` | 在 public rev49 + canonical Tide 0030 已完成后，只读核对精确账本/checksum/实存结构，再初始化 G01–G09 与 5 个已发布个性化任务码族 execution 和受限应用账号；不执行 Schema 迁移、Mock Seed 或共享模板写入 |
| `scripts/apply-production.sh` | 仅执行生产结构迁移；先校验运营 rev38 权威目录，再使用账本、SHA-256 和 PostgreSQL advisory lock 升级至 0030 |
| `scripts/test-production-migrator.sh` | 在隔离 PostgreSQL 数据库显式构造 rev38 共享目录，验证 fresh、managed upgrade、public46→teacher0028→public49→teacher0030 顺序门禁、无用对象／字段／函数门禁与结构恢复、execution ID 原位保留、checksum、工单函数 owner 和生产连接保护 |
| `scripts/verify.sh` | 验证共享表、过程关联、角色权限、乐观锁、审计/Outbox 和消息回写 |
| `scripts/rollback-test.sh` | 在临时库验证空库升级和逐级回滚 |
| `scripts/grant-tit-teacher-crud.sql` | 由共享表 Owner/DBA 执行的最小权限脚本 |

## 本地执行

```bash
cp database/.env.example database/.env
# 确认目标是本机 tide_dev 后，将 ALLOW_MOCK_SEED 改为 true
docker compose -f database/compose.yaml up -d
bash database/scripts/apply.sh
bash database/scripts/verify.sh
bash database/scripts/rollback-test.sh
```

`.env` 不进入 Git。`apply.sh` 仅接受数据库名 `tide_dev`，且必须显式设置
`ALLOW_MOCK_SEED=true` 才会执行。`apply.sh` 当前升级至 `0030`，但仍是本地
Mock 入口，不能用于公司或生产库。

## 生产迁移

生产只使用 `scripts/apply-production.sh`。它要求独立迁移账号连接串
`TIDE_MIGRATION_DATABASE_URL` 和显式目标库
`TIDE_MIGRATION_EXPECTED_DATABASE`，并同时支持：

- fresh：世文共享 `public` 表已存在，但尚无 TIDE 业务结构；
- managed upgrade：已有 `tide.schema_migrations` 账本，逐项核对原文件 SHA-256
  后只执行待应用迁移。

迁移器在每个迁移事务内获取同一个 PostgreSQL advisory lock，并把结构变更与账本
写入原子提交。已执行文件的顺序、文件名或 checksum 改变时立即停止；发现已有 TIDE
结构却没有账本时也停止，不根据表面对象自动伪造迁移历史。

迁移器还要求运营端已经提供 rev38 权威固定任务目录：稳定行
`G01:v1`–`G10:v1` 必须精确映射到当前 G01–G09 和 retired G00，且已发布
`MANDATORY_GROWTH` 只能是 G01–G09。目录不一致时，即使是 fresh TIDE Schema 也
停止；教师端不会替运营端改写共享目录。

`0020_product_analytics` 的历史定义依赖
`public.teacher_metric_snapshots`，而 public 47 会删除该旧表，因此首次建库或尚未记录 0020
的库必须严格按以下跨链顺序执行：

```text
public Alembic 46 -> teacher 0028 -> public head 49 -> teacher 0030
```

若旧快照已经不存在且 0020 尚未记录，生产迁移器会在任何 DDL 前失败关闭。已有完整
teacher 0028 账本的数据库允许在 public 47 删除旧快照后继续执行 teacher 0029–0030，
不会把已退役对象重新变成永久前置条件。历史 `0020` 文件和 checksum 保持不变。

非测试模式还会同时校验：

- URI 中 `sslmode=verify-full` 恰好出现一次，且当前 PostgreSQL 会话确实使用 TLS；
- `current_user` 与 `session_user` 都是 `tide_migrator`，不是通过高权限账号
  `SET ROLE` 伪装；
- `tide_migrator` 可登录但不是 superuser，且没有建库、建角色、复制或绕过 RLS；
- `current_database()` 精确等于 `TIDE_MIGRATION_EXPECTED_DATABASE`。

生产迁移清单明确包含 `0022–0030`，并永久排除历史
`0017/0018`，因为这两项会修改世文持有的 `public.task_assignments`。迁移器不创建
角色、不设置角色密码、不导入 Mock、不执行题库／FAQ／任务内容 Seed。DBA 必须事先
创建 `tit_teacher_crud`、`tit_growth_app`、`tide_migrator` 与
`tide_support_ticket_owner`，教师端权限继续单独审核
`scripts/grant-tit-teacher-crud.sql`。`tide_support_ticket_owner` 必须
`NOLOGIN`、无高权限且不继承任何其他角色；`tide_migrator` 必须由 DBA 授予该 owner
的成员关系，并获得 TIDE Schema 结构变更和必要授权的 grant option，以便迁移后把
四个 SECURITY DEFINER 函数移交给固定 owner；它不需要且不得拥有 superuser、
`CREATEDB`、`CREATEROLE`、`REPLICATION` 或 `BYPASSRLS`。`0023`
只向 `tit_growth_app` 授予共享工单 `SELECT` 和运营追加函数 `EXECUTE`，`0024`
则只给函数 owner 执行这些函数所必需的表列权限，并在移交后撤销其 `public.CREATE`。
迁移完成后必须重跑 `grant-tit-teacher-crud.sql`：教师运行账号只读
`tide.schema_migrations`，不得插入、更新或删除迁移账本。

`0024` 和 `0025` 的 down 都是有意的 no-op：前者不会恢复 NULL CAS／NULL message
绕过或 superuser 函数 owner；后者不会把当前 assignment 重新路由到旧任务语义。

DBA 预置角色属性示例（密码或认证材料必须通过独立秘密管理流程配置）：

```sql
CREATE ROLE tide_support_ticket_owner
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE tide_migrator
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT tide_support_ticket_owner TO tide_migrator;
```

```bash
export TIDE_MIGRATION_DATABASE_URL='postgresql://tide_migrator@db.example/tide_production?sslmode=verify-full'
export TIDE_MIGRATION_EXPECTED_DATABASE='tide_production'
bash database/scripts/apply-production.sh
```

也可以从 `backend/` 构建一次性迁移镜像。镜像只复制生产迁移 SQL 和迁移脚本，
以 UID/GID `10001` 的非 root 用户运行；认证材料只在运行时从受控 env file 注入：

```bash
docker build -f Dockerfile.migrate -t tide-teacher-migrate .
docker run --rm \
  --env-file /absolute/path/to/teacher-migration.env \
  tide-teacher-migrate
```

`teacher-migration.env` 至少包含
`TIDE_MIGRATION_DATABASE_URL=postgresql://tide_migrator@db.example/tide_production?sslmode=verify-full`
和 `TIDE_MIGRATION_EXPECTED_DATABASE=tide_production`，不得进入镜像或 Git。

生产前可在隔离 PostgreSQL 16 实例运行：

```bash
bash database/scripts/test-production-migrator.sh
```

## 公司测试库

- 公司测试库配置放在本地 `database/.env.company-test`，该文件不进入 Git，权限必须为 `600`。
- 目标库必须先按 `public 46 → teacher 0028 → public 49 → teacher 0030` 完成正式分阶段迁移；fresh、旧前缀、无账本或合并前旧编号账本均不能交给初始化脚本自动认领。
- `apply-company-test.sh` 硬限制已批准测试实例中的 `tit_growth_test_v2` 与 `postgres` owner，只接受 public `20260807_49_unused_columns` 与精确 28 条 canonical Tide 0030 账本。它逐项核对顺序、文件名、SHA-256 和最终实存结构后，以只读模式读取已发布的 G01–G09 与 5 个个性化任务码族写入 execution，再配置并验收 `tit_teacher_crud`；它不打开或执行任何迁移 SQL。
- 初始化器不得与 public/Tide migrator 并发运行；受控部署必须先完成迁移并释放迁移窗口，再执行初始化器。
- 日常应用账号固定为 `tit_teacher_crud`，只读教师身份资料和 `teacher_scorecard_current / teacher_lesson_score_current`，只获得契约允许的共享任务权限和 `tide` Schema 业务表权限；不读取原始积分、课程事实或评分配置表。
- 内部测试后端的 `TIDE_DATABASE_URL` 和 `SHIWEN_READ_DATABASE_URL` 均由该配置生成并指向同一公司测试库；运行时不再使用本地 PostgreSQL 或本地数据回退。
- 本地 `apply.sh` 会写 Mock Seed，不得用于公司测试库。

```bash
bash database/scripts/apply-company-test.sh
bash database/scripts/import-company-test-faq.sh
```

积分规则更新和历史数据重算由世文负责。教师端不再提供课程积分重算脚本；视图刷新后，应用下一次查询直接读取最新结果。
