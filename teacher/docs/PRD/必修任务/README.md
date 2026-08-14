# 任务级 PRD 索引

本目录存放固定必修任务及正式上线的个性化任务模板 PRD。目录路径暂保留“必修任务”以避免现有链接失效。总 PRD 负责系统共性和任务体系，本目录负责每个任务的具体内容与完成规则。

## 文档关系

```text
教师端系统总 PRD
  → 任务级 PRD 索引（本文件）
    → 单个固定必修／个性化任务 PRD
      → 对应代码、配置、素材和测试
```

- [教师端系统总 PRD](../新师训练营_教师端系统_PRD.md)
- [任务级 PRD 模板](_必修任务PRD模板.md)
- [协作开发标准](../../../COLLABORATION_STANDARD.md)

## 任务 PRD 清单

2026-08-11 与当前共享目录对齐，教师端展示连续编号的 G01–G09 共 9 个固定任务。G04 是 `Lesson Preparation`；重排前旧 G05 以隐藏 G00 只读保留，`free-trial-training` 不进入当前目录。

| 编码 | 任务 ID | 任务名称 | 任务类型 | 文档状态 | 业务口径 | 负责人 | 任务 PRD | 开发状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `G01` | `profile-credentials` | Profile & Credentials Completion 教师档案与资质完善 | 固定必修 | 已确认 | 部分确认 | 嘉荷 | [任务 PRD](TASK-profile-credentials_教师档案与资质.md) | 四项完成条件，3 分；只读取 TESOL 真实状态，在阔知课程 407 完成考试，站内完成 Essay 确认和完成证明提交；不展示 Self-intro |
| `G02` | `platform-policies` | Platform Policies 平台规则 | 固定必修 | 评审中 | TIDE 原生文档 | 嘉荷 | [任务 PRD](TASK-platform-policies_平台规则.md) | 版本化双语政策文档，读到底自动完成，2 分 |
| `G03` | `student-types` | How to handle different types of students 不同类型学员应对 | 固定必修 | 评审中 | 阔知 | 嘉荷 | [任务 PRD](TASK-student-types_不同类型学员应对.md) | 课程 655；三段视频和三份配套考试均完成后自动完成，2 分 |
| `G04` | `lesson-preparation` | Lesson Preparation 首课准备 | 固定必修 | 已确认 | 已确认 | 嘉荷 | [任务 PRD](TASK-lesson-preparation_首课准备.md) | 同一任务内两个独立模块：照片 AI 四项检查、课件准备确认；可任意顺序操作，两项全部通过后完成并获得 3 分；不包含设备网络检测 |
| `G05` | `ttp-orientation` | TTP Orientation TTP 入门培训 | 固定必修 | 评审中 | 阔知 | 嘉荷 | [任务 PRD](TASK-ttp-orientation_TTP入门培训.md) | 阔知 iframe 视频，3 分 |
| `G06` | `me-culture` | ME Culture & PARSNIP | 固定必修 | 评审中 | 阔知 | 嘉荷 | [任务 PRD](TASK-me-culture_ME文化与PARSNIP.md) | 阔知 iframe 多课程视频＋考试，4 分 |
| `G07` | `reliability-training` | Reliability Training | 固定必修 | 评审中 | 阔知部分映射 | 嘉荷 | [任务 PRD](TASK-reliability-training_Reliability培训.md) | 阔知 iframe 视频；考试 ID 待补，3 分 |
| `G08` | `cocos-training` | Global Communicator Training | 固定必修 | 评审中 | 阔知 | 嘉荷 | [任务 PRD](TASK-cocos-training_Cocos课程培训.md) | 阔知 iframe 视频＋考试，5 分 |
| `G09` | `set-fundamentals` | SET Teaching Fundamentals | 固定必修 | 待配置 | 阔知待发布 | 嘉荷 | [任务 PRD](TASK-set-fundamentals_SET教学基础.md) | 不使用本地 Mock；等待阔知课程／视频／考试任务 ID，5 分 |

### 历史兼容与候选资料

| 编码／任务 ID | 任务名称 | 当前口径 | 资料 | 处理方式 |
| --- | --- | --- | --- | --- |
| 重排前 `G02` / `device-network` | Device & Network Check 设备与网络检测 | 已合并进当前 G04 | [历史任务 PRD](TASK-device-network_设备与网络检测.md) | 不再作为独立任务；仅保留历史设计资料 |
| 重排前 `G05` / 当前 `G00` | Lesson Preparation 首课准备 | 已合并进当前 G04 | [当前 G04 PRD](TASK-lesson-preparation_首课准备.md) | 只读保留历史 assignment，不生成独立入口 |
| `free-trial-training` | Free Trial Training 体验课培训 | Domestic pending，不计入当前 9 项必修 | [候选任务 PRD](TASK-free-trial-training_体验课培训.md) | 仅保留候选课程资料；正式启用时必须配置阔知课程与考试任务，业务确认前不展示、不计数、不计分 |

### 个性化改善任务

| 任务范围 | 业务口径 | 任务 PRD | 开发状态 |
| --- | --- | --- | --- |
| 课中质量、两类可靠性、九类用户反馈匹配学习及拉黑问题教师安全复核 | 13 类教师端任务已确认；L0／L1 纯运营项和不可逆提醒不进入教师端 | [联合任务 PRD](TASK-personalized-improvement_个性化改善任务Mock.md) | 世文按真实数据触发 assignment；教师端不再使用 `mockAssigned` 作为正式分配 |

文档状态统一使用：`草稿`、`评审中`、`已确认`。  
业务口径统一使用：`Mock`、`部分确认`、`已确认`。

## 新增任务 PRD

1. 复制 `_必修任务PRD模板.md`；该模板同时支持固定必修和个性化任务。
2. 命名为 `TASK-<任务ID>_<任务名称>.md`，例如 `TASK-platform-policies_平台规则.md`。
3. 填写任务内容、流程、素材、完成规则、异常处理和验收用例。
4. 在上方任务清单中登记负责人、状态和文档链接。
5. 由 Echo 检查是否涉及公共架构，由业务负责人确认任务内容。
6. 任务实现变化时，同步更新任务 PRD；任务 PRD 和代码尽量在同一个 PR/MR 中提交。

## 口径原则

- 总 PRD 定义系统共性，任务 PRD 不重复改写公共规则。
- 任务 PRD 定义单个任务的个性化内容和完成判定。
- 个性化任务由嘉荷维护内容与配置；任务是否触发、分配、排序及其教师安全原因仍以世文下发为准。
- 个性化任务不计分；固定必修任务只展示世文返回的确定积分。
- 两者出现冲突时，先记录冲突并请 Echo 确认，不直接用任务 PRD 覆盖总 PRD。
- 未确认的题目、分数、日期、素材和结果需要标注 Mock。
- 世文安全视图和跨系统字段尚未确认的部分保留“待确认”，不在任务 PRD 中自行定义。
