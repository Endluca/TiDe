# 教师端与运营端同机部署

状态：**部署骨架与技术加固已建立，联合门禁已固定到 public
`20260823_100_scope_snapshot_diff`、教师端 `0043_p_rel_execution_catalog` 和唯一当前
`G01–G09` 目录。跨所有权迁移必须严格按 public 46 → teacher 0028 → public 50 →
teacher 0032 → public 54 → teacher 0037 → public 55 → release public 56 → teacher 0038 →
release public 57 → teacher 0040 → teacher 0041 → public 59–65 → teacher 0042 →
public 66–99 → teacher 0043 → public 100 执行；
完整链和数据库契约探针未通过前禁止上线。**

## 结论

同一台服务器可以承载两套应用，但不能把它们合成一个进程、一个写账号或两份任务事实。
推荐使用一个公司网关或 Edge Nginx，只开放 80/443，按域名分流：

```text
Internet
  |
  v
company gateway / TLS
  |
  v
edge:8080
  |-- ops.example.com
  |     |-- /api/* -> api:8010
  |     `-- /*      -> web:8080
  `-- teacher.example.com
        |-- /api/* -> teacher-api:3000
        `-- /*      -> teacher-web:8080

api -----------\
score-worker ---+--> one logical PostgreSQL database
teacher-api ---/     public = shared/TiDe facts
                       tide = teacher execution facts
```

`3000`、`8010` 和 `5432` 只在容器网络或公司内网可见。两个 Web 容器都监听
容器内 `8080`，不会形成宿主机端口冲突。Edge 默认只绑定宿主机 `127.0.0.1`；
若公司网关不在同机，只能改为经网络控制验证的私网监听地址，不能使用 `0.0.0.0`。
预检只接受 loopback 或 RFC1918 的规范 IPv4；公网、链路本地、全网卡和 IPv6 地址均
失败关闭。IPv6 要等公司网关、宿主机防火墙、Compose 发布语法和真实 IP 链一起完成专项
验收后再启用，不能只把监听值改成 `::`。
生产 PostgreSQL 建议使用公司内网数据库；即使数据库也在同一主机，仍不得发布 `5432`。

## 教师端 0025–0043 迁移门

TiDe 的唯一当前目录是连续 `G01–G09`，其中 `G04` 为首课准备，只保留授课环境拍照 AI
审核和课件准备确认两个模块。
教师端旧目录 `G01-G04,G06-G10` 的执行记录不能只改展示文案，否则会把教师执行流程、
完成状态和积分结算路由到不同业务任务。教师端必须通过向前迁移
`0025_fixed_task_semantic_alignment`，按稳定共享模板行调整 execution 的业务编码，
保留 execution ID、步骤/规则 ID 和已有进度。

经两端契约确认的映射是：

```text
旧 G01 -> 当前 G01
旧 G03 -> 当前 G02
旧 G04 -> 当前 G03
旧 G02 -> 当前 G04
旧 G06 -> 当前 G05
旧 G07 -> 当前 G06
旧 G08 -> 当前 G07
旧 G09 -> 当前 G08
旧 G10 -> 当前 G09
旧 G05 -> G00，只保留退役历史
```

该映射必须由 0025 在单一事务和生产迁移账本内应用；不能由部署脚本暗改，不能重建
execution ID，也不能清空教师已有进度。0025 是向前语义迁移，生产回退依赖发布前一致性
备份或向前修复，不恢复旧任务路由。

0026 保存阔知课程同步事实，0027 删除已停用的本地题库运行时；0028 退役依赖
旧教师快照的业务变化视图，0029 删除经空表、G00 路由和外部依赖门禁验证的
无消费者对象，0030 无 `CASCADE` 删除可还原冗余列与孤儿函数。随后 0031 在稳定
`G02:v1` / 当前 G04 execution 上原位发布三个互不阻塞的模块：设备与
连接基础预检、授课环境照片四项 AI 审核、备课须知确认。迁移保留 execution、既有 step/rule
主键、assignment 与进度原始行；未知结构整笔拒绝。旧版设备或备课证据不会冒充当前版本，
已完成 assignment 的终态仍保留且无需重做。

0032 创建 `tide.account_onboarding_states`，只将迁移前已有 `LOGIN/SUCCESS`
事件的账号回填为 `FIRST_LOGIN` v1 / `MIGRATED_EXISTING`。已注册但从未成功
登录的账号不回填，仍应在首次登录后展示引导。引导状态是账号级一次性
终态事实；运行角色使用表级 CRUD，Trigger/约束只接受读取和幂等插入语义。

0033 在稳定 `G01:v1` execution 上原位更新外部状态规则，使 G01 只消费 TESOL，
并保留 execution、step/rule ID、assignment 和已有进度。Self-intro 源字段仍由
源数据 owner 保留，但教师运行角色不再读取，G01 页面与完成判断也不再展示或消费。

0037 在同一稳定 G04 execution 上原位删除当前 `g02-device-check` 步骤定义，将照片审核和
课件准备重排为两个独立模块，并把完成规则收窄为这两项。迁移不删除历史设备进度或设备证据，
不修改 assignment、终态或分值；历史 0031 保持不变以校验既有账本和迁移来源。

0038 将共享任务 `P-FB-NEGATIVE:v1` 的教师端执行配置升级为授课环境拍照检测：已有库
原位保留 execution、assignment 和进度；fresh 库在合法共享模板存在时使用目录同源的
确定性 UUID 建立 execution。仅精确的稳定变体或两个受治理历史标签允许展示拍照入口，
其他标签继续失败关闭；照片沿用 G04 的四项 AI 审核档案。缺失／非法共享模板、身份冲突或
未知旧结构都会整笔拒绝。

0039/0040 将 G02 收敛为带版本和内容哈希的原生文档阅读，并由阅读到底事实和跨表约束
校验完成状态。0041 新增 CRM SSO 一次性交换事实、账号来源和会话认证方式；它允许 SSO
账号不设置本地密码，同时保留原有密码登录路径，不修改任务、积分、课程或教师执行状态。
0042 将 G09 发布为阔知课程 658。0043 保留既有可靠性 assignment、execution identity 和
步骤进度，将 `P-REL-MEMO` 发布为带版本/哈希的 Lesson Memo 原生文档，并将
`P-REL-ATTENDANCE` 发布为阔知课程 595 入口；课程 595 的考试 ID 未提供前继续关闭自动完成。

当前工作树已经落地以下技术门禁：

1. 教师端正式迁移器永久排除 `0017/0018` 对 `public.task_assignments` 的 DDL，并包含
   从 `0001` 到 `0043` 的 38 条完整有序生产链、迁移账本、checksum 与 advisory lock。
   `0027` 删除已退役的本地 Quiz 运行时；`0028` 只退役依赖旧教师快照的
   `tide.analytics_task_business_change_v1`；`0029` 在空表、G00 路由和外部依赖门禁后删除
   6 张无消费者表与 5 个已被 v2 替代的视图，均不删除 public 表。
   `0030` 在确认所有文件都为私有、无外部列依赖和无函数消费者后，无 `CASCADE`
   删除 `tide.file_objects.visibility` 与孤儿 `tide.enforce_outbox_target()`；`0031`
   原位升级 G04 三模块，`0032` 创建账号级首次登录引导状态，`0033` 收窄 G01
   TESOL-only 校验规则，`0037` 将当前 G04 收敛为照片审核和课件准备两个模块；
   `0038`–`0043` 依次完成个性化环境拍照、G02 文档/阅读状态、CRM SSO、G09 课程 658 和
   两条 P-REL execution。
2. 运营回复工单函数在同一事务设置 `WAITING_TEACHER`、最后回复时间和 48 小时截止时间，
   `tit_growth_app` 按最终表级 ACL 授权，Trigger 强制运营回复走原子函数。
3. 教师端生产配置对双数据库、严格 SSL、HTTPS 公共地址和 OSS fail-closed；readiness
   同时检查两条数据库连接。
4. 教师端 API 的后台调度器虽然仍嵌在 HTTP 进程，但所有全局任务都通过
   `tide.job_leases` 竞争数据库租约；只有当前持租约副本执行，续租失败立即停止，其他副本
   可接管。G04 与个性化环境图片均走任务提交校验，不再有独立照片队列。联合部署固定完整升级到 public 100 / teacher 0043。

切流前仍需关闭两项：

1. 在目标库按顺序执行到 public 100 / teacher 0043：先在 public 50 / teacher 0032
   完成历史 G04 三模块链，再通过 public 54 / teacher 0037 将当前 G04 收敛为照片审核与
   课件准备两个模块，再用 public 55 收敛源宽表，执行 public 56 / teacher 0038 的个性化环境拍照，
   再执行 public 57 / teacher 0039–0040 的 G02 原生文档和 teacher 0041 的 CRM SSO 结构，
   然后依次执行 ACL/DTS 合并迁移到 public 59、国内学生隐私迁移到 public 60、public 61/62、public 63 direct 隐私门禁、public 64 G05/G08 课程文案和 public 65 G09 课程文案；在 public 65 执行 teacher 0042，再连续升级 public 66–99、执行 teacher 0043，最后升级 public 100。验证 0025 保留 execution ID、步骤/规则 ID 和教师进度，同时验证 rev47–100 与 0027–0043 完成本地 Quiz
   退役、旧视图和空置对象清理、G01 TESOL-only 收窄、G04 两模块收敛、引导状态建表与个性化环境拍照发布，
   DTS 状态表和最终表级 ACL，且未越权改写共享业务事实。
2. 教师端主 PRD 仍描述“TIDE 刷新后再修改工单状态”，需要与已落地的原子回复函数同步，
   不能同时保留两套状态时序口径。

`preflight.sh` 会正向核对固定提交中的完整 38 条 / 0043 迁移清单、精确 G01–G09 标题/分值、
0033 G01 TESOL-only 规则、0037 G04 两模块规则、0038 个性化拍照契约、0039/0040 G02 原生文档、
0041 CRM SSO 结构、0042 G09 课程配置、0043 两条 P-REL execution 和契约探针固定的 public 99 head；
`contract-probe.sql` 会在目标库正向核对完整迁移账本、共享目录、assignment 和 execution。
任一通过都不替代另一个，也不替代备份恢复演练和真实压测。
完整的发布前证据、主键级前后对照和停止条件见
[教师端任务语义迁移与联合部署验收](../../docs/教师端任务语义迁移与联合部署验收-20260730.md)。

## 数据库所有权

| 对象 | 唯一迁移所有者 | 运行写入者 |
|---|---|---|
| `public.task_templates/task_assignments`、积分、通知、审计、Outbox、读取视图 | `tide_sys_admin` | 最终文档的表级权限；业务边界由 Trigger/约束落实 |
| `tide.*` | `tide_sys_admin` | `tit_teacher_crud` |
| `public.teacher_support_tickets` 与原子函数 | `tide_sys_admin` | 教师端写事实；运营端只调用函数 |

数据库角色固定收敛为五个：

- `tide_sys_admin`：已有管理账号，仅在受控发布窗口执行两条迁移链和只读契约探针；
- `tide_support_ticket_owner`：非登录、非超级的共享工单函数 owner；
- `tit_growth_app`：运营 API、积分结算和 SourceWide Worker 共用的 TiDe 后端账号；
  按最终表级 ACL 读取源表、写派生结果；业务写边界由 Trigger/约束保护；
- `tit_teacher_crud`：教师 API，按最终文档获得 public 指定表与 `tide.*` 的表级权限；
- `tit_dts_ingest_runtime`：DTS 消费入库，只对两张源宽表与四张接入状态表拥有 CRUD，不能写 Outbox 或其他业务表。

TiDe 和教师端 migration 都核对 `current_user=session_user=tide_sys_admin`、目标库与连接传输。
一般目标仍要求 `sslmode=verify-full`；固定 `tide_system_test` PRE 专线端点可使用
`sslmode=disable`，但角色、主机、端口、库名和实际非 TLS 状态必须全部匹配。契约探针复用
同一管理凭据并强制使用只读事务；专线明文探针显式设置
`TIDE_CONTRACT_PROBE_REQUIRE_SSL=false`。任何一个条件不符都必须在执行 DDL 或探针查询前停止。
固定 PRE 明文迁移与探针还要求清除 `PGHOST`、`PGHOSTADDR`、`PGPORT`、`PGDATABASE`、
`PGUSER`、`PGSERVICE`、`PGSERVICEFILE`，防止 libpq 环境变量绕过 URI 的固定端点。

初始连接上限：

- 运营 API：`2 workers * (5 pool + 2 overflow) = 14`；
- 积分 Worker：`1`；
- SourceWide Worker：`1`；
- 教师 API：两个池各 `5`，共 `10`；
- 运行时理论峰值 `26`，不含迁移、监控、备份和 DBA。

该预算必须低于 PostgreSQL `max_connections` 的 70%–80%，否则先缩池，不能靠提高
数据库连接上限掩盖等待。契约探针只在发布门禁临时占用一个管理连接，不进入
常驻运行预算。

## 准备

1. 优先检出整仓 `Tide_teachers_camp`，其中运营端位于仓库根目录、教师端位于
   `teacher/`；也继续支持两个独立仓库。无论采用哪种布局，都必须固定到已评审提交，
   不要从浮动分支直接构建。整仓模式下将 `TIDE_TEACHER_REPO_PATH` 指向绝对路径
   `/部署路径/Tide_teachers_camp/teacher`，`TIDE_TEACHER_EXPECTED_COMMIT` 填整仓提交 SHA；
   预检会检查整个整仓工作树无未提交改动。
2. 复制 `.env.example` 到安全目录外的部署变量文件，只填写非敏感坐标，并显式设置 Edge
   的 loopback 或私网监听地址。`TIDE_EDGE_NETWORK_SUBNET` 必须与主机、公司网络和其他
   Docker 网络不重叠；运营 API 只信任该网络中固定的 `TIDE_EDGE_PROXY_IP`，不能信任整个
   RFC1918 地址段。`TIDE_COMPANY_GATEWAY_CIDR` 只填写公司网关实际源 IP（推荐 `/32`）
   或最小必要的 `/24`–`/32` 私网段。
3. 创建三个受保护环境文件：TiDe 后端运行（`tit_growth_app`）、教师端运行
   （`tit_teacher_crud`）和管理迁移（`tide_sys_admin`）；权限至少为仅部署账号可读。
   SourceWide Worker 复用 TiDe 后端运行文件，教师端迁移和只读契约探针复用管理迁移
   文件。填写后的文件不得留在仓库工作树。
4. 配置两个域名和 TLS。`edge` 只接收公司网关转发，宿主机不得把其 HTTP 端口直接
   暴露到公网。
5. 教师端公共素材必须先上传到 HTTPS CDN，并完成浏览器读取验证。
6. 教师端联合部署固定 `SHIWEN_READ_MODE=DIRECT_TABLES`。公司网关必须覆盖而不是追加
   不可信客户端传入的转发头；Edge 的 `real_ip` 只接受
   `TIDE_COMPANY_GATEWAY_CIDR`，随后把 `X-Forwarded-For` 覆盖成单个规范客户端 IP。
   运营 API 只信任固定 Edge IP，教师 API 只信任 Edge 这一跳。直连 Edge 的请求即使自行
   携带 XFF 也只能得到直连来源地址。代理拓扑变化时必须同步修改并重跑真实 IP 限流验收。

教师端工单一次允许三张、每张 8 MiB 图片。Edge 与教师 Web 的总请求上限统一为 26 MiB，
并关闭请求体预缓冲，避免在 Edge tmpfs 复制完整请求；应用层仍逐文件执行 8 MiB 限制。
当前 `teacher-api` 内存预算为 1 GiB，默认最多同时解析 4 个 multipart 请求
（`TIT_TEACHER_MULTIPART_UPLOAD_MAX_CONCURRENCY=4`）。提高该值前必须用三张 8 MiB 的
最坏请求做并发 RSS/OOM 验收，不能只按平均图片大小估算。

加载非敏感变量后执行：

```bash
set -a
. /安全路径/combined.env
set +a

bash deploy/combined/preflight.sh
docker compose -f deploy/combined/docker-compose.yml config --quiet
```

教师端未按完整顺序到 public 100 / teacher 0043、最终账本不是精确 38 条、G04 仍含当前设备步骤、
目录缺项、个性化拍照、G02 文档或 P-REL execution 契约不精确、仍含 G10 或任一标题/分值语义错误时，应在预检阶段停止，
这是预期结果。

## 发布顺序

1. 评审 public 100、教师端 0043、任务编码、G01 TESOL-only 读取过滤、G04 两模块、
   个性化拍照、G02 文档与阅读状态、CRM SSO、源宽表 v1.2、DTS 状态和最终数据库角色；
   固定包含完整修复的新提交 SHA。
2. 停止两端写流量、教师后台任务和积分结算 Worker。
3. 创建一致性备份，记录 Alembic head、教师迁移账本和任务目录快照；验证恢复路径。
4. DBA 预建或确认 `pg_trgm`。
5. TiDe Alembic 先只升级到 revision 46，保留历史 teacher 0020 回放所需的旧快照表：

   ```bash
   docker compose -f deploy/combined/docker-compose.yml \
     --profile migration run --rm migrate \
     alembic upgrade 20260807_46_teacher_g01_source
   ```

   禁止在新库先升级到 public 55；否则旧快照表已删除，teacher 历史迁移 0020 无法建立原视图，
   teacher migrator 会失败关闭并提示正确分阶段顺序。

   revision 38 只会在没有既有固定任务积分事实时自动对齐分值；若迁移拒绝，必须先走
   受治理的积分规则发布与同事务全量重算。G04 的历史三模块文案位于 revision 50，当前
   两模块标题和文案位于 revision 54；G01 TESOL-only 文案与教师源列权限变更位于
   revision 51，会在第 8 步随 public 54 链一起执行；个性化任务文案位于 revision 55，
   只在第 9 步随 public 55 执行。

6. 将 `TIDE_TEACHER_MIGRATION_TARGET` 临时设为 `0028_retire_task_business_change_view`，
   使用 `tide_sys_admin` 运行教师端正式迁移；迁移 `tide.*` 和共享工单例外，不得执行
   `0017/0018` 的共享任务表 DDL。迁移器必须确认严格 SSL、目标库、固定非超级账号和受限
   SECURITY DEFINER owner：

   ```bash
   docker compose -f deploy/combined/docker-compose.yml \
     --profile migration run --rm teacher-migrate
   ```
7. 确认 teacher 账本完整到 0028 且
   `tide.analytics_task_business_change_v1` 不存在后，先把 TiDe Alembic 从 46 升到
   `20260810_50_g04_sections`：

   ```bash
   docker compose -f deploy/combined/docker-compose.yml \
     --profile migration run --rm migrate \
     alembic upgrade 20260810_50_g04_sections
   ```

   public 47 删除 `teacher_metric_snapshots`、`lesson_facts` 和
   `lesson_dimension_scores`；rev48 继续收紧表结构，rev49 在可还原性核验后删除
   `complaint_category_rules.learning_title / learning_url` 与 `operator_sessions.last_seen_at`；
   rev50 更新稳定 G04 行的历史三模块文案。然后把
   `TIDE_TEACHER_MIGRATION_TARGET` 设为 `0032_first_login_onboarding` 并再次运行
   `teacher-migrate`，让历史 0031 在它评审过的 public 50 文案上完成三模块升级。

8. 确认 public 50 / teacher 0032 同时就绪后，将 TiDe Alembic 升到 public head 54，
   再把 `TIDE_TEACHER_MIGRATION_TARGET` 设为 `0037_g04_remove_device_check` 运行
   `teacher-migrate`。public 54 链内的 rev51 先将 G01 文案与读取权限收窄为 TESOL-only，
   rev54 再原位发布照片审核和课件准备两模块文案；teacher 完整链依次执行 0033 与 0037，
   先收窄 G01 规则，再删除当前设备步骤定义并收窄 G04 completion rule。确认账本 head 为 0037、
   0029 删除的 6 张废弃表和 5 个旧分析视图均不存在，且 0030 删除的
   `file_objects.visibility` 和 `enforce_outbox_target()` 也不存在，0031/0032/0033/0037 分别完成 G04
   历史三模块、账号引导事实、G01 TESOL-only 和当前两模块收敛。0028 down 只能在 public 47
   之前验证，生产回退使用备份或向前修复。

9. 确认 public 54 / teacher 0037 同时就绪且当前 G04 已不再返回设备步骤后，严格按
   `20260811_55_source_wide_v12` → `20260811_56_p_fb_negative_copy` → teacher 0038 →
   `20260811_57_g02_document` → teacher 0040 → teacher 0041 → public 59–65 → teacher 0042 → public 66–99 → teacher 0043 → public 100
   继续。public 55 收敛教师源宽表，release public 56 更新稳定 `P-FB-NEGATIVE:v1`
   文案，0038 发布个性化授课环境拍照；release public 57 发布 G02 精确文案，0039/0040
   原位切换到版本化文档并增加阅读状态约束，0041 新增 CRM SSO。最后一步才将 ACL/DTS
   分支与 release 内容分支合并到 `20260812_59_simple_acl`，再应用
   `20260813_60_dom_privacy`、`20260814_61_teacher_copy`，最后应用
   `20260818_62_dts_claim_idx`，启用 direct 前再应用 `20260819_63_dts_direct_privacy`，
   然后依次应用 `20260819_64_g05_g08_courses` 和 `20260819_65_g09_set_course`，在该精确
   public 65 切换点执行 teacher 0042；之后将 Alembic 连续升级到
   `20260822_99_blacklist_three_state`，再执行 teacher 0043，最后应用
   `20260823_100_scope_snapshot_diff`。不得在 teacher 0037 之前执行
   public 55；最终契约探针只接受 public 100 / teacher 0043。

10. 确认 teacher 账本精确为 38 条且 head 为 0043，再以只读共享目录模式核对执行内容：
   0033 G01 TESOL-only、0037 G04 两模块且无当前设备步骤、0038 个性化环境拍照、
   0039/0040 G02 文档与阅读状态、0041 CRM SSO 结构、0042 G09 课程配置，以及 0043 的
   P-REL-MEMO 文档 execution 和 P-REL-ATTENDANCE 课程 595 execution；execution/保留 step/rule ID、
   assignment 与历史进度原始行均必须符合快照。
   `TASK_CATALOG_PUBLIC_WRITE` 必须为 `false`，不得再以迁移器外脚本改写共享任务编码或补灌 execution 配置。
11. DBA 在两条完整迁移链结束后统一应用最终表级权限；由于 0024 改变了函数 owner，权限脚本
   必须在本次迁移后重跑，即在 DBA 自己的受控数据库会话中执行
   `$TIDE_TEACHER_REPO_PATH/backend/database/scripts/grant-tit-teacher-crud.sql`，否则教师角色
   不会获得工单教师消息函数的执行权。随后复用管理迁移文件运行只读数据库契约探针：

   ```bash
   docker compose -f deploy/combined/docker-compose.yml \
     --profile migration run --rm contract-probe
   ```

   探针会核对 `current_user=session_user=tide_sys_admin`、目标库、实际 TLS、只读事务和
   三个运行账号的精确权限边界。`SET ROLE` 或非 TLS 会话执行都会失败。

12. 先启动并观察积分 Worker 与 SourceWide Worker，再启动运营 API、一个或多个教师 API 副本和两个 Web。多个
   `teacher-api` 副本必须连接同一逻辑 PostgreSQL，并在扩容前确认 0022 的
   `tide.job_leases` 已落库、后台租约与照片任务行租约可正常续租和接管：

   ```bash
   docker compose -f deploy/combined/docker-compose.yml \
     up -d score-settlement source-wide api teacher-api web teacher-web
   ```

13. 最后启动 `edge`，由公司网关按域名灰度切流。验证运营登录、教师登录、9 项任务、
    状态更新、幂等结分、积分/课程读取和工单往返全链路后再全量。

## 回滚与观测

应用镜像可以回退到兼容版本；数据库变更使用向前修复，不把 destructive down migration
当生产回滚方案。需要回到旧数据结构时使用发布前一致性备份。

灰度期间至少观察：

- 两端 P50/P95/P99、5xx 与超时；
- PostgreSQL 活跃/空闲/等待连接、锁等待、慢 SQL 和事务时长；
- 积分 Outbox 与 `source_wide.changed.v1` 待处理数、最老事件年龄、两个 Worker 的 heartbeat/readiness；
- 教师后台租约、图片任务积压和最近成功时间；
- Nginx 上游失败、请求体大小与静态资源命中率；
- 容器 CPU、RSS、OOM、重启次数和日志写入速率。
