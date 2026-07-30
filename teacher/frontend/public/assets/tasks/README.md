# 任务视频素材接入

培训视频地址由后端已发布的任务配置返回，并全部在当前任务页内播放。正式素材未发布时使用明确标注的 Mock 视频，播放器、进度保存和完成校验仍保持可验收；正式素材通过新版本地址替换。

| 任务 ID | 章节数 | 预留目录 / 文件 | 当前对应课程 |
| --- | ---: | --- | --- |
| `device-network` | 3 | `/assets/tasks/device-network/01-*.mp4` 至 `03-*.mp4` | 498 + 526 + 596 |
| `platform-policies` | 1 | `/assets/tasks/platform-policies/training-video.mp4` | 499 Platform Policies |
| `lesson-preparation` | 2 | `/assets/tasks/lesson-preparation/01-*.mp4` 至 `02-*.mp4` | 400 + 500 |
| `ttp-orientation` | 1 | `/assets/tasks/ttp-orientation/training-video.mp4` | 513 TTP |
| `me-culture` | 6 | `/assets/tasks/me-culture/01-*.mp4` 至 `06-*.mp4` | 520 + 398 |
| `reliability-training` | 2 | `/assets/tasks/reliability-training/01-*.mp4` 至 `02-*.mp4` | 595 Reliability |
| `free-trial-training` | 10 | `/assets/tasks/free-trial-training/01-*.mp4` 至 `10-*.mp4` | 324 + 510 |
| `cocos-training` | 7 | `/assets/tasks/cocos-training/01-*.mp4` 至 `07-*.mp4` | 630 Global Communicator |
| `set-fundamentals` | 1 | OSS 版本化 Mock 视频槽 | SET Teaching Fundamentals |

多视频课程采用固定顺序章节：首次观看不能跳过未完成章节；每章结束后自动播放下一章；所有章节完成后统一解锁对应小测或完成状态。所有题目都在任务页内作答，不提供外部考试入口。当前 Mock 通过线统一使用 80%。
