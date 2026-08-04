# 公共接口合同

## 优先级

1. 产品范围和教师表达以主 PRD 为准。
2. 跨系统任务和写入边界以项目根目录的《教师端共享任务表契约》为准。
3. HTTP 字段以 `openapi.yaml` 为准，`task-contract.ts` 是 TypeScript 镜像。
4. `examples/` 只是 Mock 合同示例。

OpenAPI `0.12.0` 已对齐共享 PostgreSQL 架构、教师工单、一期产品埋点合同及阔知多课程免登／进度同步。当前固定任务为连续编号的 `G01–G09`，教师端只读取关联已发布模板的这 9 个任务；重排前退役任务以隐藏 `G00` 只读保留。5 个已发布个性化任务码族通过动态标题和触发证据区分 13 类个性化任务。

## 前端使用要点

- `taskInstanceId` 就是共享 `task_assignments.assignment_id`，不强制 UUID 格式。
- 任务状态直接使用共享表枚举，初始值是 `ASSIGNED`。
- `TaskContext.assignment` 始终存在；不再有 `assignmentSync`。
- assignment 的 `display_title` 优先于模板标题；关联课程和安全触发事实优先读取 `evidence_snapshot`，关联提醒只补充 `teacher_safe_facts` 与 `related_courses` 白名单字段。
- 任务写操作带 `Idempotency-Key`、`commandId` 和 `expectedStateVersion`。进度保存是纯过程写入，不增加共享任务版本。
- 文件使用“Upload Intent → 上传 → SHA-256 确认 → 鉴权下载”闭环。
- 外部和系统消息合并返回；已读与点击均可幂等回写。
- 登录后行为事件使用 `/api/v1/app-events`；登录前页面与技术异常使用 `/api/v1/app-events/anonymous`。事件必须携带 Schema 版本和标签页会话，任务归属与版本由后端校验／补充。
- 工单使用 `/api/v1/support-tickets`；运营回复只通过共享表原子追加函数写入，教师端刷新读取，不创建独立系统通知。
- G02、G05、G06、G07、G08 通过 `/kuozhi-launch` 获取多课程免登地址，通过 `/kuozhi-progress` 读取或刷新服务端判定；票证只由后端生成。示例模式只展示，不回写当前老师任务状态。

## 主要错误码

| 错误码 | HTTP | 含义 |
|---|---:|---|
| `AUTH_REQUIRED` | 401 | 登录态缺失或失效 |
| `TASK_NOT_FOUND` | 404 | 任务不存在或不属于当前教师 |
| `IDEMPOTENCY_CONFLICT` | 409 | 同一幂等键收到不同请求 |
| `STATE_VERSION_CONFLICT` | 409 | 共享任务版本已变更，需重新读取 |
| `INVALID_STATE_TRANSITION` | 409 | 当前状态不允许该操作 |
| `FILE_NOT_READY` | 409 | 作为输出的文件尚未完成校验 |
| `SOURCE_UNAVAILABLE` | 503 | 教师资料、指标或课程安全视图不可用 |

错误响应不得包含连接串、堆栈、密码、令牌、原始世文 payload 或学生信息。

本地检查：

```bash
ruby verify-contracts.rb
```
