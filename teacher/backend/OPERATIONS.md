# TIDE 后端运行与交接手册

## 1. 当前定位

当前交付是 NestJS 模块化单体，公共后端已覆盖账号、My TIDE、任务、文件、共享数据库事件、BytePlus ModelArk Responses API、图片审核扩展和仅文字 FAQ。

嘉荷继续负责固定必修任务和个性化任务的具体流程、题目、素材、提示词、完成规则与任务级验收；公共后端只提供模板、状态机、文件、校验处理器和数据边界，不在代码中代填具体业务规则。

## 2. 本地启动

依赖：Node.js 20 以上、pnpm、Docker 和 PostgreSQL 16 客户端。

```bash
cd backend
pnpm install --frozen-lockfile
cp database/.env.example database/.env
cp .env.example .env
docker compose --env-file database/.env -f database/compose.yaml up -d
bash database/scripts/apply.sh
pnpm start:dev
```

本地密码只写入被 Git 忽略的 `.env`。不要把真实数据库连接、JWT 密钥、AI 密钥或邮件凭据提交到仓库。

启动后检查：

```bash
curl http://localhost:3000/health
curl http://localhost:3000/health/ready
curl http://localhost:3000/health/dependencies
```

`/health` 只表示进程存活；`/health/ready` 只有在 TIDE 核心表、世文教师身份来源和两个积分读取视图都可查询时才返回 ready。任一连接、对象或读取权限未配置或不可用均返回 503，避免流量进入只能提供部分数据的实例。

`/health/dependencies` 展示数据库、文件存储、AI、邮件和后台任务开关的实际状态。它只汇总配置与最近一次真实调用结果，不会为了探活上传文件、调用 AI 或发送邮件，也不会阻止服务启动。

### Mac mini 内部测试

内部测试使用公司测试 PostgreSQL 和 Cloudflare Quick Tunnel。TIDE 自有表与世文共享表位于同一个数据库，不再连接 Mac mini 本地 PostgreSQL：

> 临时部署口径（2026-07-28）：公司服务器资源申请完成前，测试后端固定运行在当前 Mac mini，Codex Sites 前端通过 Quick Tunnel 访问该后端。公司邮件接口暂依赖 Mac mini 的 hosts 映射 `192.168.27.200 mg.51talk.me`，并使用 HTTP 地址；迁移到公司服务器时必须改用正式内网 DNS／服务地址，移除对本机 hosts 和 Quick Tunnel 的依赖。

```bash
cd backend
pnpm provision:internal-test
./scripts/deploy-internal-test-backend.sh
```

- 下一次明确授权的升级必须从当前 public 50 / teacher 0032 继续，并保持
  `public 46 → teacher 0028 → public 50 → teacher 0032 → public 54 → teacher 0037 → public 55 → public 56 → teacher 0038 → public 57 → teacher 0040 → teacher 0041` 十二阶段顺序；
  public 54 包含 rev51，teacher 0037 包含 0033，public 55 收敛源宽表。完成迁移并结束迁移窗口后，再运行
  `database/scripts/apply-company-test.sh` 只读核对精确 36 条 canonical 账本/结构并初始化 execution
  和受限账号；两者禁止并发，该脚本不再创建或升级 `tide` Schema。本次代码交付未执行数据库升级。
- `provision:internal-test` 使用 `TIDE_DATABASE_URL` 连接公司测试库，只为库中真实存在的教师创建测试账号，不复制或改写 `public.teachers`，也不生成教师可见的站内通知。
- 后端与隧道由 `com.aiec.tide-internal-backend`、`com.aiec.tide-internal-tunnel` 两个 LaunchAgent 常驻。
- 源代码合并后重新运行部署脚本，会同步运行副本并重启后端。
- Quick Tunnel 重启后地址可能变化。只有发布跨域 Codex Sites 前，才应把最新地址写入 `VITE_API_BASE_URL`，并把 Sites 的精确来源写入 `TIDE_INTERNAL_CORS_ORIGINS` 后重新部署；Gaea/Nginx 同源发布不读取该构建变量。
- 公司测试库使用 `tit_teacher_crud` 读取教师资料以及 `teacher_scorecard_current / teacher_lesson_score_current`；积分接口不启用本地快照或其他兜底。

## 3. 环境配置

- 基础：`NODE_ENV / PORT / LOG_LEVEL / CORS_ORIGINS`。
- TIDE 数据库：`TIDE_DATABASE_URL` 及连接池、连接超时和 SQL 超时。
- 世文读取：内部测试的 `SHIWEN_READ_DATABASE_URL` 与 `TIDE_DATABASE_URL` 使用同一公司测试库 `tit_teacher_crud` 连接。`SHIWEN_READ_MODE` 只影响教师身份资料来源；积分总览和逐课明细始终固定读取 `public.teacher_scorecard_current / public.teacher_lesson_score_current`。生产环境仍需单独完成安全评审。
- 反向代理：直连本地开发保持 `TRUST_PROXY_HOPS=0`。联合生产拓扑中，公司网关先覆盖客户端转发头，Edge 只信任显式网关源 CIDR、解析真实地址并把 `X-Forwarded-For` 覆盖为单个客户端 IP；教师 API 因此固定 `TRUST_PROXY_HOPS=1`。Edge 不得追加客户端传入的 XFF。代理链变化必须同步调整并验证 `request.ip`，否则全体教师会共享同一限流桶或信任伪造地址。
- 账号：`AUTH_JWT_SECRET / DATA_HASH_SECRET`、令牌有效期、允许邮箱域名和公开前端地址。
- 文件：`FILE_STORAGE_PROVIDER`、`LOCAL_FILE_STORAGE_DIR`、大小、MIME 和上传意图有效期。启用私有 OSS 时配置 `OSS_REGION / OSS_ENDPOINT / OSS_BUCKET / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET`；Key 只进入 `backend/.env.local` 或公司密钥系统。
- 当前内部测试机不在阿里云 VPC 内时使用地域公网 Endpoint `https://oss-ap-southeast-1.aliyuncs.com`；盖娅与新加坡 OSS 同地域且确认内网互通后，生产环境换成 `https://oss-ap-southeast-1-internal.aliyuncs.com`。
- 公共素材先执行 `pnpm upload:public-assets` 预检，再执行 `pnpm upload:public-assets -- --apply` 上传。脚本只接收批准的图片、音视频和 PDF，保留 `frontend/public` 相对路径，按 SHA-256 跳过相同对象；覆盖同名不同内容必须显式增加 `--overwrite`。
- 外部目录可用 `--source=/absolute/path --prefix=videos` 指定来源与 OSS 前缀；题库源文档不在允许扩展名中，不会进入公共 Bucket。
- 系统通知：`SYSTEM_NOTIFICATION_PUBLISHER_ENABLED / SYSTEM_NOTIFICATION_CONFIG_PATH / SYSTEM_NOTIFICATION_POLL_INTERVAL_MS / SYSTEM_NOTIFICATION_BATCH_SIZE`。配置时间必须使用 `+08:00`，正式环境先校验配置再启用 Publisher。
- 个性化任务提醒：`PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED` 默认关闭；启用时必须设置带时区的 `PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT`，并可用 `PERSONALIZED_TASK_NOTIFICATION_POLL_INTERVAL_MS` 调整默认 5 分钟扫描间隔。首次 rollout 必须晚于历史任务，防止补发；调度器只读 assignment，只写 `tide.system_notifications`。
- 新阶段提醒：`GROWTH_STAGE_NOTIFICATION_SCHEDULER_ENABLED` 默认关闭，轮询间隔由 `GROWTH_STAGE_NOTIFICATION_POLL_INTERVAL_MS` 控制。首次启用只记录每位老师当前最高开放阶段，不补发历史提醒；之后最高开放阶段提升时才写入一条 `tide.system_notifications`。
- 工单清理：`SUPPORT_TICKET_CLEANUP_POLL_INTERVAL_MS / SUPPORT_TICKET_CLEANUP_BATCH_SIZE` 控制已进入截止时间的 48 小时关单和失败图片清理重试。该任务不轮询运营回复、不生成系统通知。
- 后台任务总开关：`BACKGROUND_JOBS_ENABLED` 默认开启，统一控制系统通知发布、个性化任务提醒、成长阶段提醒和工单到期清理。多 API Pod 可全部保持 `true`：四类任务由 `tide.job_leases` 分任务单活，未取得租约的副本跳过本轮。该模式要求数据库至少已应用 `0022_performance_job_leases`，不得在缺少租约表时降级成无锁执行；`BACKGROUND_JOB_LEASE_MS` 控制调度租约。G04 图片审核属于任务提交校验，结果写入 `image_reviews / image_review_items`，没有独立照片 Worker。
- AI：`MODELARK_ENABLED / MODELARK_BASE_URL / MODELARK_MODEL / MODELARK_TIMEOUT_MS / ARK_API_KEY`。默认关闭；启用时必须配置 `ARK_API_KEY`。`MODELARK_BASE_URL` 只填写到 `/api/v3`，不能追加 `/responses`；真实密钥只进入公司密钥系统或后端环境变量。

生产环境采用 fail-closed 校验，以下条件任一不满足，进程直接拒绝启动：

- `TIDE_DATABASE_URL` 与 `SHIWEN_READ_DATABASE_URL` 都存在，并且各自恰好包含一次 `sslmode=verify-full`；
- `TRUST_PROXY_HOPS=1`；联合拓扑只信任已经清洗转发头的 Edge 一跳；
- `SHIWEN_READ_MODE=VIEWS` 时显式提供 `SHIWEN_TEACHER_IDENTITY_VIEW`；联合共享库部署使用
  `DIRECT_TABLES`；
- `PUBLIC_APP_URL` 与 `PUBLIC_API_URL` 都是 HTTPS；
- `FILE_STORAGE_PROVIDER=OSS` 且 OSS 必填配置完整；
- `DATA_HASH_SECRET` 与 `AUTH_JWT_SECRET` 均为至少 32 个字符的运行时密钥。

生产环境不得使用示例连接串、开发回退密钥、临时世文 CRUD 账号或本地公开目录。`DATABASE_MAX_CONNECTIONS` 会分别作用于 TIDE 和世文两个连接池；单实例的理论连接上限约为该值的两倍，多副本上限约为 `2 × DATABASE_MAX_CONNECTIONS × Pod 数`，必须按数据库总连接预算反推每 Pod 配额。任务租约只执行短 SQL 领取和续租，不长期占用专用连接，但仍计入瞬时池压力。

### Docker 运行

生产镜像使用多阶段构建，运行阶段不包含开发依赖，并以 Node 镜像内置的非 root 用户启动：

```bash
cd backend
docker build -t tide-teacher-api:reviewed .
```

镜像只暴露 `3000`，其内置健康检查请求 `/health/ready`。生产环境中，容器进入
healthy 不只代表 Node 进程存在：public Alembic 账本必须唯一指向
`20260811_57_g02_document`，教师端迁移账本必须是完整的 36 条 canonical 清单，
包含 `0033_g01_tesol_only`、`0037_g04_remove_device_check` 和
`0038_personalized_environment_photo`，且唯一最新版本为 `0041_crm_sso_hybrid`。运营端稳定模板行必须精确对应当前 G01–G09
和 retired G00，九条当前执行配置也必须按同一稳定行处于 ACTIVE。后台任务租约、
共享工单表、账号引导状态表及固定 owner 函数必须完整，6 张废弃表和 5 个旧分析视图必须不存在，教师身份来源和两张积分读取视图
也必须可查询。教师运行账号必须只能读取两条迁移账本；对
`public.teacher_source_wide` 不得拥有整表读取或任何写权限，且有效可读列必须精确为
`tchr_id` 与 `is_cpl_tesol`，不能读取已退出 G01 契约的 `is_self_introduce`。密钥和数据库
连接只能由部署平台在运行时注入，不能写入镜像或构建参数。

所有 `multipart/form-data` 请求在 Multer 读入内存前共用进程级并发门禁。
`MULTIPART_UPLOAD_MAX_CONCURRENCY` 允许 `1–16`，默认 `4`；1 GiB 教师 API 容器应
保持默认值，除非用真实文件大小、并发和 RSS 压测证明可以调整。容量用尽返回可重试的
`429 MULTIPART_UPLOAD_CAPACITY_EXHAUSTED` 和 `Retry-After`，不排队持有请求体。

后台任务嵌在每个 NestJS API 进程中。多 Pod 部署时所有副本可设置 `BACKGROUND_JOBS_ENABLED=true`：四类全局调度任务依靠数据库租约单活并在持有者退出或租约过期后接管。所有副本必须连接同一个已按顺序应用 `0033_g01_tesol_only`、`0037_g04_remove_device_check`、`0038_personalized_environment_photo` 和 `0041_crm_sso_hybrid` 的 PostgreSQL。生产文件统一使用私有 OSS；若非生产仍使用 `LOCAL`，多 Pod 必须挂载同一 RWX 存储到完全相同的 `LOCAL_FILE_STORAGE_DIR`，RWO／各 Pod 本地盘会导致上传后由其他副本读取失败。

## 4. 迁移与发布前检查

所有结构变化只能新增版本化迁移，不改写已执行迁移。开发环境执行：

```bash
bash database/scripts/apply.sh
bash database/scripts/verify.sh
bash database/scripts/rollback-test.sh
```

`rollback-test.sh` 只在临时空库演练完整升降级，不代表可以在生产直接执行 down 脚本。生产变更必须先备份、评审增量 SQL、确认锁表影响和回退策略，再由数据库管理员执行。

`0025` 必须在运营端 rev38 目录迁移完成后执行。它只按
`public.task_templates.row_id` 原位改写教师 execution 的业务码，不改变 execution
ID、assignment ID 或历史过程记录；旧 G05 只退役为 G00，不搬迁到新 G04。
该迁移 forward-only，生产回退使用发布前备份或后续受控修复，不执行语义反向编号。

`0031` 在稳定 `G02:v1` / G04 execution 上原位发布设备网络基础预检、授课环境
照片 AI 检查、备课须知确认三个独立模块。它只接受已评审的旧两步／旧三步结构，
未知 step/rule 整笔拒绝，并用迁移前后快照确认 assignment 和 progress 原始行不变。
空 execution 目录下它 no-op，不代替显式 Seed。`0028` 同样 forward-only；不用 down 把已记录的
三模块进度重新解释为旧结构。

`0032` 新增账号级新手引导终态事实。迁移只回填已有 `LOGIN/SUCCESS`
安全事件的账号，从未成功登录的已注册账号保持无行，以便首次登录后展示引导。
运行角色只得读取和幂等插入，不得更新或删除已确认的终态事实；0032 down 只删除该表。

`0037` 在同一 G04 execution 上删除当前 `g02-device-check` 步骤定义，
完成规则只保留授课环境照片 AI 审核和课件准备确认。它不删除旧设备步骤进度，
不改 execution、assignment 或已有终态；未知结构整笔拒绝。该语义迁移同样 forward-only。

`0033` 只在稳定 G01 execution 上把外部状态规则收窄为 TESOL-only，并保留
execution、step、其他 rule、assignment 与 progress 身份；down 只恢复规则版本和失败提示。

`0038` 只在 public `20260811_56_p_fb_negative_copy` 与 P-FB-NEGATIVE 新文案
精确匹配后发布个性化授课环境拍照；down 遇到已启动 assignment、命令回执、进度、
上传或提交等执行证据时失败关闭。

`0039` 只在 public `20260811_57_g02_document` 与精确 G02 文案就绪后，将稳定
`G03:v1` / G02 execution 原位切换为单一版本化文档步骤。`0040` 增加实体化读到底字段和
延迟跨表 constraint trigger，允许同一事务内保存阅读证据并完成共享 assignment，但拒绝
提交“已读完而 assignment 未完成”的不一致状态。

本地完整验收：

```bash
bash scripts/acceptance.sh
```

部署后的最小冒烟检查：

```bash
TIDE_SMOKE_API_URL=https://api.example.com pnpm smoke
TIDE_SMOKE_API_URL=https://api.example.com TIDE_SMOKE_ACCESS_TOKEN=实际测试账号令牌 pnpm smoke
```

第一条检查存活、数据库就绪和依赖状态；提供令牌后还会检查任务列表与消息列表。令牌只通过临时环境变量传入，不写入仓库。

## 5. 任务与 FAQ 内容装载

- 任务内容通过 `task_templates / task_template_versions / task_step_definitions / task_validation_rules` 版本化发布。第一位真实老师开始后，不原地修改已使用版本。
- `AI_IMAGE_REVIEW` 只接收任务模板配置的图片步骤、提示词版本、标准版本和标准键；具体值由嘉荷任务级 PRD 决定。
- FAQ 只读取 `knowledge_documents.status=ACTIVE`、`authority_level=FAQ` 且片段未禁止回答的内容。
- 全量 FAQ 的唯一版本化内容源是 `content/faq/51Talk Teacher FAQ - Canonical.md`；运行服务不直接读取 Markdown，也不得从旧参考项目复制问答功能代码。
- FAQ 更新采用“创建新 document version 和 chunks → 校验内容哈希与问答样例 → 同一事务激活新版本并退休旧版本”，不覆盖历史版本。
- 公司测试库使用 `bash database/scripts/import-company-test-faq.sh` 导入经过校验的全量 Canonical FAQ；脚本会同时激活 `FAQ_INTENT_MATCH` 和 `FAQ_TEXT_ANSWER` Prompt 版本，可安全重复执行。
- 任务和 FAQ 当前不建设运营后台；正式内容由受控迁移或审核后的导入流程写入，禁止浏览器直连数据库。
- 系统通知一期不建设运营后台。编辑 `config/system-notifications.json` 后先运行 `pnpm publish:system-notifications` 做一次同步与到期发布；已发布配置只允许撤销，修改内容必须使用新 `configKey`。
- 个性化任务提醒启用前先核对 rollout 时间、现有 assignment 数和待生成数量；不得以空 rollout 启动，也不得向 `public.notifications` 补写关联消息。

## 6. 监控重点

- HTTP：请求量、P95/P99、4xx/5xx、`/health/ready`。
- 数据源：`source_read_status` 的连续失败次数、最近成功时间和错误码。
- 任务：提交失败率、长期 `UNDER_REVIEW`、幂等冲突和状态版本冲突。
- 图片审核：长期停留在 `CHECKING / BEAUTIFYING`、后台批次失败、审核与处理总耗时；用户上传成功后接口应立即返回，正常在约 10–15 秒内完成。
- 跨系统：`integration_events` 最新发布时间与世文消费游标的延迟、重复消费和对账差异。
- 文件：上传失败、隔离数量、磁盘容量、摘要不一致和下载拒绝。
- AI：`ai_runs` 失败率、延迟、错误码和用量；不得采集原始图片、完整提示词或模型原文。
- FAQ：未命中率、`faq_gaps` Top 问题和未解决反馈；这些数据不进入世文积分链路。
- 系统通知：长期 `SCHEDULED`、`FAILED`、发布延迟、`recipient_count` 异常和配置加载失败；不得在日志输出正文、受众明细或数据库连接。
- 个性化任务提醒：扫描失败、扫描时长、新增数量异常和 dedupe 冲突趋势；日志不得输出教师 ID、标题、Why、evidence 或截止时间证据。

灰度阶段建议先配置以下最低告警：5 分钟内 5xx 比例超过 2%；P95 超过 2 秒持续 5 分钟；`/health/ready` 连续 3 次失败；图片审核超过 60 秒仍未结束；文件、AI 或邮件最近状态为 `error`。通知渠道、值班人和故障联系人仍需在部署平台确认。

## 7. 备份、恢复与故障处理

- PostgreSQL：至少备份 `tide` Schema，并定期在隔离环境验证恢复；世文 Schema 由世文侧独立负责。
- 私有文件：数据库元数据和私有对象必须使用同一恢复点；本地暂存阶段需同时备份 `LOCAL_FILE_STORAGE_DIR`。
- 事件：未被世文消费的 `integration_events` 不得删除或原地修改；恢复后由世文按游标和事件 ID 重读。
- 世文视图异常：My TIDE 返回最近安全投影并标记缓存；没有投影时返回明确不可用，不伪造实时结果。
- AI 异常：图片审核进入人工复核；FAQ 返回固定拒答或安全来源提示，不把技术故障判成老师失败。
- 文件空间不足：先停止新上传并保留数据库，扩容或迁移后校验 SHA-256；不要直接删除对象键对应文件。

## 8. 生产部署前仍需外部确认

- 世文正式安全视图、只读角色、连接限制和一条真实 assignment 联调样例。
- 世文消费游标、轮询频率、结分幂等、对账和故障联系人。
- 公司邮件服务准确请求合同和真实模板。
- BytePlus ModelArk 测试／正式密钥、Responses API 图片输入及真实连通性验证。
- 私有 OSS、签名访问、保留期和删除策略。
- 盖娅部署规格、域名、日志、告警、备份恢复和密钥管理。
- 嘉荷负责的每个任务级 PRD、素材、规则配置和业务验收样例。

以上项目不阻塞公共后端继续开发，但未完成前不能宣称生产上线就绪。
