# 源码快照来源

本仓库于 2026-07-30 以干净快照方式首次导入，目标是把运营端与教师端放在同一代码库，
同时保留两个应用各自的技术栈、迁移链和运行边界。

## 导入来源

| 目录 | 来源分支 | 来源基线提交 | 导入内容 |
|---|---|---|---|
| 仓库根目录 | `tit_growth_system` 的 `codex/production-readiness` | `cc638b79aec576da20fca8d64cd6ad57f9df376d` | 该基线及其当前已完成但尚未提交的系统改动 |
| `teacher/` | `new-teacher-training-camp-prototype` 的 `codex/combined-production-readiness` | `f211ed8cafc964b4c5a69861fd904f04a280c5c0` | 该基线及其当前已完成但尚未提交的教师端改动 |

两个旧仓库的 Git 历史没有合并。本仓库的首次提交是完整、可审查的当前源码快照，
避免把旧仓库已删除的二进制文件和无关历史对象带入新仓库。

## 明确不导入

- `.env`、`.env.local`、数据库凭据、Token 和其他本地敏感配置；
- `node_modules/`、`.venv/`、`dist/`、运行日志与临时文件；
- Playwright 截图、设计核对图、表格检查结果等 QA 派生产物；
- 教师端运行存储、临时设计参考和 Sites 项目绑定；
- 已删除或应由 HTTPS CDN 承载的 MP4、WebM、MOV 训练素材。

`teacher/backend/reference/task-quiz-banks.json` 是数据库迁移会读取的正式题库源，
属于可部署系统输入，因此随源码保留。

生产发布仍需在固定提交上执行
[`deploy/combined/README.md`](deploy/combined/README.md) 中的迁移、权限、契约探针、
备份恢复、压测与灰度验收。源码已上传不等于生产环境已经完成部署或切流。
