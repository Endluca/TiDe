# 运行、数据与安全

## 推荐交付方式

业务方运行当前真实测试数据需要四样东西：

1. Git 仓库中的脱敏代码快照；
2. 单独安全交付、不会进入 Git 的 `TiDe.env`；
3. 能访问公司测试 PostgreSQL 的网络；
4. 独立的数据库运行账号和运营登录账号。

OpenAI Key 不是当前确定性任务触发所必需。默认
`AGENT_PROVIDER=deterministic` 即可运行全部当前业务规则。

## 环境变量

仓库只提供无密钥的 `.env.example`。真实文件至少包含：

- `DATABASE_URL`
- `APP_ENV`
- `TIT_SESSION_TTL_HOURS`
- `AGENT_PROVIDER`

模型相关变量均为可选。真实环境文件应复制为 `backend/.env.local`，或把其路径传给
`scripts/setup.sh` 和 `scripts/start.sh`。

Gaea application 运行时使用 `gaea/application` 模块（根 `gaea/Dockerfile` 暂作兼容入口）构建
运营端、教师端和积分 Worker，并由 s6 在同一个 Pod 中管理五个常驻进程；海外、国内 DTS 项目
都选择独立的 `gaea/dts-ingest` 轻量模块，海外项目必须位于新加坡、国内项目必须位于中国大陆；
模块、镜像或区域环境变量不能替代平台地理放置。
轻量模块只包含受限 Python 接入进程。运营与教师域名分别绑定 `8010/8080`，教师 NestJS 的
`3000` 不对外开放。同一项目和镜像支持整套 Pod 使用 2 个或更多副本及 RollingUpdate：
积分候选进程通过 PostgreSQL session advisory lock 保持逻辑单活，未持锁 standby 仍刷新
本 Pod heartbeat；教师全局调度使用 `tide.job_leases`，照片处理按数据库行租约认领。
生产变量必须通过 Gaea 配置/密钥管理注入，完整清单见 `gaea/README.md`。共享源码或构建模块不代表
共享数据库账号，也不自动执行 TiDe Alembic 或教师端 migration；但同一 UID 的进程会继承
容器级变量，所以这个合并形态只用于受控 TEST，不提供生产级秘密隔离。

DTS 启动探针按 `DB → TCP → Kafka` 执行：先使用运行时数据库密钥完成目标库连接、传输、身份与
Catalog/ACL 检查，再完成 bootstrap DNS 解析并从当前 Pod 对解析结果做无凭据 TCP 连接，最后
使用 SASL 密钥校验 Kafka。5 秒 TCP 连接预算不覆盖前置 DNS 解析。日志/readiness 只允许安全摘要
和稳定错误码，不输出密码、账号、Broker 解析地址或底层驱动原文。TCP 探针不收发应用数据，
后续 Kafka 探针按 bootstrap 认证、Metadata、partition、advertised broker 认证、coordinator 与
offset 请求分段；Metadata 与 `kcat -L -t <topic>` 使用同类单 Topic Metadata API 语义，但不创建
带密码的 kcat 配置文件。探针只读取
metadata/offset，不消费业务消息、不产生目标写入、不推进消费组位点；Pod Ready
只是基础设施和契约可达证据，不是 CDC 或业务完成证据。国内进程在 Kafka 探针成功后、ready 前
只幂等登记一条不含密钥或学生标识的 HMAC fingerprint 契约行。

国内消息中的原始学生 ID 只能短暂存在于国内容器内存。国内进程必须在构造任何海外 PostgreSQL
SQL 参数前，用 CSPRNG 生成、以 64 位小写 hex 表示的国内项目独占 32-byte HMAC 密钥生成
`dom:v1:<HMAC-SHA256>` 并删除
原值；海外项目、海外数据库、日志和错误 payload 不得持有该密钥或原始国内学生 ID。稳定 token
仍属于伪名数据，只能用于必要的去重、收藏/拉黑归因和课程关联。若安全评审不允许稳定 token
跨境，必须改为国内状态库完成聚合，海外只接收不可回链的指标结果。首次启动会在受限 DTS 状态
中登记密钥的单向 fingerprint；以后启动必须精确匹配，禁止直接替换密钥。轮换必须另行设计
token 版本和存量关联迁移。国内 `cancel_reason/reason_desc` 自由文本同样不原样出境：只保留
精确业务值 `Unfilled Lesson Memo`，其他非空内容统一替换为不含原文的存在标记。

国内到海外 PostgreSQL 的连接默认及正式环境必须使用 `sslmode=verify-full`。当前固定
`tide_system_test` PRE 端点走受控专线，允许应用、教师端及国内/海外 DTS 使用明文连接；专线降低
暴露面但不加密 PostgreSQL 流量，不能把“走专线”写成“传输已加密”。DTS 必须同时设置
`TIT_DTS_INGEST_DB_SSLMODE=disable` 与 `TIT_DTS_ALLOW_INSECURE_DB=true`，缺一、端点/库不匹配或
非 PRE 均失败关闭；application、教师端、迁移与契约探针也只允许各自连接串对该固定端点设置
`sslmode=disable`，不得用全局开关放宽其他目标。数据库启用 TLS 后必须把所有连接恢复为
`verify-full` 并重新验证实际会话 TLS。国内项目仍固定 `TIT_DTS_EXECUTION_REGION=cn`、仅国内持有
HMAC 密钥且 `TIT_DTS_PROJECTION_ENABLED=false`；只有海外项目可以在双 checkpoint 门禁通过后
持有全局投影锁。

固定 PRE 明文路径还必须在联网前拒绝 `PGHOST`、`PGHOSTADDR`、`PGSERVICE` 等 libpq 连接身份
环境覆盖；否则即使版本化 URI 命中白名单，驱动仍可能把凭据发往另一个目标。

多副本私有文件优先使用 OSS；LOCAL 模式必须让所有 Pod 把同一块 ReadWriteMany 共享卷挂载
到 `/var/lib/tide`，RWO 或每 Pod 独立目录都不满足跨副本读取和清理。视频预热账本若从发布
Job 执行也必须使用共享 RWX 状态目录；积分 heartbeat 则必须留在本 Pod `/tmp`，不得共享。

## 数据边界

- 当前教师和课程数据是一次性测试基线，不是每日实时数据。
- 数据库存放批次、允许跨境的字段当前态、教师指标快照、课程事实和触发结果；国内学生 ID 只存
  `dom:v1:` 伪名 token，不属于无损原始行。
- 页面中的“多来源数据”表示多张事实表合并，不表示自动缺失。
- 原始学生信息和 Excel 不进入 Git；前端不返回原始学生 ID；海外库不保存原始国内学生 ID。
- 业务方测试库中的操作会真实写入该测试库，但不代表生产动作或真实通知已送达。

## 禁止上传

- `.env`、`.env.local`、API Key、数据库密码和登录密码；
- Excel、JSON 导出、数据库 dump 或原始学生数据；
- `.runtime/` 日志、截图、缓存、测试报告和构建产物；
- 本机绝对路径、钥匙串服务名或个人账号信息。
