# 新师训练营教师端｜后端开工准备包

## 1. 目的

本目录最初用于完成业务后端开发前的五个准备阶段。准备包之后，公共 NestJS 后端已经完成；当前实现、运行方式和最终验收分别见 [`backend/README.md`](../../backend/README.md)、[`backend/OPERATIONS.md`](../../backend/OPERATIONS.md)和[公共后端开发验收报告](验收/2026-07-21_公共后端开发验收报告.md)。

## 2. 事实依据

1. [主 PRD](../PRD/新师训练营_教师端系统_PRD.md)
2. [教师端共享任务表契约](../接口与数据/教师端共享任务表契约.md)
3. [数据库表结构](../接口与数据/数据库表结构.md)
4. [数据清单与关系设计](../技术方案/02_数据清单与关系设计.md)
5. [模块边界与数据归属](../技术方案/03_模块边界与数据归属.md)
6. [协作开发标准](../../COLLABORATION_STANDARD.md)

本目录是 2026-07-21 的开工准备记录，其安全视图、事件发布视图和任务副本方案已被 2026-07-22 共享任务表契约取代。冲突时按项目 `AGENTS.md` 中的事实优先级处理。

## 3. 阶段与交付物

| 阶段 | 交付物 | 完成标准 |
|---|---|---|
| 1. 协作边界 | `COLLABORATION_STANDARD.md` 的 2.3、2.4 和后端隔离目录 | 嘉荷只通过公共接口和 Mock 开发，不能直连数据库 |
| 2. 数据库设计 | [`backend/database`](../../backend/database/README.md) | 空库可执行迁移，核心约束可验证 |
| 3. 公共任务合同 | [`backend/contracts`](../../backend/contracts/README.md) | 固定和个性化任务使用统一任务上下文 |
| 4. 世文集成合同 | [`backend/contracts/世文集成契约.md`](../../backend/contracts/世文集成契约.md) | 世文安全视图读取、TIDE 事件发布视图和双方权限边界明确 |
| 5. 本地环境 | `backend/database/compose.yaml`、验证脚本和验收报告 | PostgreSQL 16 建库、迁移、Seed、约束和回滚通过 |

## 4. 后端开工门槛

- 数据所有权和嘉荷访问边界无歧义。
- 初始迁移可从空数据库完整执行。
- 固定任务、个性化任务和文件引用有稳定公共结构。
- OpenAPI、TypeScript 类型和示例一致。
- 共享数据库三条读取通道已定义；正式安全视图、只读角色、消费游标等参数集中标记为待确认。
- Mock 数据不能进入真实世文发布视图。
- PRD 与接口文档联动检查通过。

以上开工门槛已经通过，`backend/` 现同时保存正式 NestJS 源码、数据库、合同、测试和参考资料。本节保留为开工历史，不再代表当前实现状态。

## 5. 本地数据库快速使用

```bash
cd "backend/database"
cp .env.example .env
# 只在本机修改 .env 中的开发密码，不提交 .env
docker compose -f compose.yaml up -d
bash scripts/verify.sh
```

停止容器使用 `docker compose -f compose.yaml stop`。不要随意执行 `down -v`，因为它会删除本地数据库卷。
