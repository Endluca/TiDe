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
都选择独立的 `gaea/dts-ingest` 轻量
模块，只包含受限 Python 接入进程。运营与教师域名分别绑定 `8010/8080`，教师 NestJS 的
`3000` 不对外开放。同一项目和镜像支持整套 Pod 使用 2 个或更多副本及 RollingUpdate：
积分候选进程通过 PostgreSQL session advisory lock 保持逻辑单活，未持锁 standby 仍刷新
本 Pod heartbeat；教师全局调度使用 `tide.job_leases`，照片处理按数据库行租约认领。
生产变量必须通过 Gaea 配置/密钥管理注入，完整清单见 `gaea/README.md`。共享源码或构建模块不代表
共享数据库账号，也不自动执行 TiDe Alembic 或教师端 migration；但同一 UID 的进程会继承
容器级变量，所以这个合并形态只用于受控 TEST，不提供生产级秘密隔离。

DTS 启动探针使用运行时注入的数据库与 SASL 密钥完成真实连接，但日志/readiness 只允许安全摘要
和稳定错误码，不输出密码、账号、Broker 解析地址或底层驱动原文。探针只读取数据库 Catalog、
ACL 与 Kafka metadata/offset，不消费业务消息、不产生目标写入、不推进消费组位点；Pod Ready
只是基础设施和契约可达证据，不是 CDC 或业务完成证据。

多副本私有文件优先使用 OSS；LOCAL 模式必须让所有 Pod 把同一块 ReadWriteMany 共享卷挂载
到 `/var/lib/tide`，RWO 或每 Pod 独立目录都不满足跨副本读取和清理。视频预热账本若从发布
Job 执行也必须使用共享 RWX 状态目录；积分 heartbeat 则必须留在本 Pod `/tmp`，不得共享。

## 数据边界

- 当前教师和课程数据是一次性测试基线，不是每日实时数据。
- 数据库存放批次、无损原始行、教师指标快照、课程事实和触发结果。
- 页面中的“多来源数据”表示多张事实表合并，不表示自动缺失。
- 原始学生信息和 Excel 不进入 Git；前端不返回原始学生 ID。
- 业务方测试库中的操作会真实写入该测试库，但不代表生产动作或真实通知已送达。

## 禁止上传

- `.env`、`.env.local`、API Key、数据库密码和登录密码；
- Excel、JSON 导出、数据库 dump 或原始学生数据；
- `.runtime/` 日志、截图、缓存、测试报告和构建产物；
- 本机绝对路径、钥匙串服务名或个人账号信息。
