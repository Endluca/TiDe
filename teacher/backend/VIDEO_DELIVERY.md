# 视频交付与预热

## 新视频发布

先生成视频：

```bash
pnpm prepare:public-video-v2
pnpm archive:public-video-masters -- --apply
```

预检发布清单：

```bash
pnpm release:public-video
```

正式执行：

```bash
pnpm release:public-video -- --apply
```

脚本严格按以下顺序执行：

1. 上传清单中的新版本视频。
2. 校验全部 CDN URL 返回 `206 Range`。
3. 使用阿里云 `PushObjectCache` 提交海外 CDN 预热。
4. 使用 `DescribeRefreshTasks` 查询到全部成功。
5. 最后在一个数据库事务中切换 `assetUrl` 和 `mediaVersion`。

任何一步失败都不会切换数据库，也不会删除旧版本。

视频版本目录使用一年不可变缓存。修改视频时必须发布新版本目录，不能覆盖已经发布的文件。

## CDN 操作后的自动回热

CDN 缓存清理或配置变更的操作脚本，成功后调用：

```bash
pnpm rewarm:public-video-after-cdn-change -- \
  --event-id=<变更单号或操作ID> --apply
```

该入口从数据库读取当前正在使用的 v2 URL，不读取待发布版本。`cleanup:public-video-v1` 已内置此回热；回热失败会恢复已删除的 v1 对象。

## 集中开营手动预热

在首批老师进入前 1–2 小时执行一次：

```bash
pnpm prefetch:public-video-for-camp -- \
  --event-id=<开营批次ID> --apply
```

不设置每天或每周定时任务。

## 重试、幂等与日志

- 相同 `trigger + event-id + manifest version + URL` 只提交一次。
- 查询超时后重新执行会继续查询原任务 ID，不重复提交。
- 明确失败并修复原因后，添加 `--retry-failed` 重试。
- 状态保存在 `VIDEO_PREFETCH_STATE_DIR`，生产环境应指向持久卷。
- `events.jsonl` 逐条记录 URL、任务 ID、状态、进度和时间；`pipeline-events.jsonl` 记录上传、Range 校验、预热、数据库切换阶段。
- 阿里云和数据库凭证只从环境变量读取，不写入代码或日志。

当前 RAM 调用仍返回 `403 Forbidden.RAM`。代码和离线测试可先完成；运维授权后再执行带 `--apply` 的真实联调。默认预热海外常规节点，只有明确需要 L2 时才添加 `--l2`。

## v1 清理

v2 切换并观察满 24 小时后，使用 `cleanup:public-video-v1` 清理公开 v1 和 CDN 缓存。脚本要求存在成功的新版本预热记录、显式确认 v2 已在线并提供最早清理时间；任何清理或回热失败都会从私有母版恢复已删对象。
