# TIDE 后端

教师端 NestJS 后端，包含账号、My TIDE、9 个教师可见固定任务（G01–G09）和已触发个性化任务的执行、文件、FAQ、消息和行为事件。重排前退役任务以隐藏技术编码 G00 只读保留，不进入教师端任务列表、阶段完成数或“可获得积分”。部署、监控和交接见[OPERATIONS.md](OPERATIONS.md)。

## 架构边界

- 两端共用同一 PostgreSQL。教师端自有表全部放在 `tide` Schema。
- 任务模板与实例直接使用 `public.task_templates/task_assignments`，不再保留本地任务副本或 HTTP 下发/回传链路。
- 任务步骤、进度、视频心跳、答案、文件和审核结果保存在 `tide`，通过 `task_assignment_id` 关联共享任务。
- 所有考试均在阔知完成；TIDE 不保存题库、标准答案或作答记录，考试完成只依据阔知返回的 `percent=100`。
- G01 只读取 `public.teacher_source_wide.is_cpl_tesol`；Self-intro 状态继续保留在源表，
  但不在 G01 中读取、展示或用于完成判断。
  源表没有更新时间时，接口的新鲜度时间明确返回 `null`，不使用其他表时间冒充。
  `public.notifications` 只按世文业务文字提醒读取，已读与点击可回写；TIDE 消息只写 `tide.system_notifications`。
- 教师资料通过 `ShiwenTeacherReadAdapter` 读取；My TIDE 总览和逐课明细固定读取 `public.teacher_scorecard_current / public.teacher_lesson_score_current`。积分接口没有原始表重算、评分配置读取或本地快照兜底。
- 教师端不写 `score_entries`。任务状态用共享 `row_version` 做乐观锁，并由数据库写审计与 Outbox。
- 当前固定任务目录以世文库为准：G02 Platform Policies、G03 Student Types、G04 Lesson Preparation、G05 TTP、G06 ME、G07 Reliability、G08 Cocos、G09 SET。G04 只保留授课环境照片 AI 审核和课件准备确认两个模块，不包含设备网络检测。个性化任务只读取任务触发中心已为当前老师创建的 assignment，教师端不自行触发或为所有老师预置全部模板。
- 个性化任务提醒调度器默认关闭。启用前必须设置明确 rollout 时间；调度器只读 assignment 的状态、截止时间和时区证据，生成的新任务／到期提醒通过唯一幂等键写入 `tide.system_notifications`。
- 成长阶段提醒调度器默认关闭。首次启用只记录当前最高开放阶段，不补发历史消息；之后老师因在营天数或完成上一阶段而开放新阶段时，只生成一条阶段提醒。
- G04 首课画面和 `P-FB-NEGATIVE` 的已确认授课环境变体使用当前任务提交的 `AI_IMAGE_REVIEW` 校验。G04 仍将结果保存在 `image_reviews / image_review_items` 并保持既有 PASS 复用逻辑；个性化变体仅使用本次照片检测，读取后删除照片对象、不写审核明细、不复用历史 PASS，本次四项 PASS 即完成。个性化入口只接受稳定变体或两枚精确历史标签，其他差评标签继续失败关闭。旧 G00 照片队列、滤镜产物和独立 Worker 已退役。
- 产品行为和后端权威结果统一写 `tide.app_events`；当前使用 analytics v2 任务旅程／漏斗和保留的技术质量、帮助使用只读视图，不写任务、积分或课程业务表。
- 教师工单直接使用双方共用的 `public.teacher_support_tickets`；TIDE 创建教师消息、同步未读、关单并清理 `support-tickets/` 私有图片，世文只追加运营消息。

## 本地开发

```bash
pnpm install
cp .env.example .env
cp database/.env.example database/.env
docker compose -f database/compose.yaml up -d
bash database/scripts/apply.sh
pnpm start:dev
```

`TIDE_DATABASE_URL` 是应用业务连接。`SHIWEN_READ_DATABASE_URL` 用于教师资料和两张世文当前视图读取。生产环境必须为两个连接配置合适的受限账号。

## 主要接口

- `GET /api/v1/auth/crm-sso`：验证 CRM 短时 JWT，仅跳转到携带一次性兑换码的前端回调。
- `POST /api/v1/auth/crm-sso/exchange`：一次性兑换 TIDE 自己的 access/refresh token；CRM JWT 不进入前端会话。
- `GET /api/v1/auth/capabilities`：返回当前 `HYBRID` / `CRM_SSO_ONLY` 能力，供前端隐藏或保留旧入口。
- `/api/v1/tasks`：共享任务列表、详情、开始、进度、提交、校验和重试。
- `/api/v1/me/*`：教师资料、G01、My TIDE、课程和消息。
- `/api/v1/files`：上传意图、校验和鉴权下载。
- `/health/dependencies`：数据库、文件、AI、邮件和后台任务开关状态。

私有文件默认写入本地目录。切换公司 OSS 时，把 `.env.example` 中的 OSS 配置复制到 `backend/.env.local`，将 `FILE_STORAGE_PROVIDER` 改为 `OSS`，再填写 RAM 用户的 AccessKey ID 和 AccessKey Secret。密钥不得写入前端、提交 Git 或发送到聊天。
- `/api/v1/faq`：纯文字 FAQ。
- `/api/v1/app-events`：幂等保存登录后白名单行为事件。
- `/api/v1/app-events/anonymous`：保存登录前页面和技术异常事件。
- `/api/v1/support-tickets`：教师创建、读取、补充、已读、关单和鉴权查看工单图片。

BytePlus ModelArk 默认关闭；启用时由后端通过 OpenAI SDK 调用 Responses API，模型输出使用严格 JSON。`ARK_API_KEY` 只从运行环境注入；模型服务异常时图片审核进入 `UNDER_REVIEW`，不自动判教师失败。

## 验收

```bash
bash scripts/acceptance.sh
```

该脚本会执行迁移、格式/构建、单测、E2E、契约、数据库权限、回滚和 PRD/接口同步检查。
