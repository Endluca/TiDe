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
