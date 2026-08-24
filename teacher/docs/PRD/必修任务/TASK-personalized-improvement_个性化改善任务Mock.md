# 联合任务 PRD｜个性化改善任务

> 文件名中的 `Mock` 为历史路径，暂保留以避免现有链接失效。当前模板范围已确认；前端内置的示例教师、课程、日期、指标值和完成结果仍是 Mock，不是真实分配数据。

## 0. 文档信息

| 项目 | 内容 |
| --- | --- |
| 任务范围 | 教师端“我的任务”中的个性化改善任务 |
| 模板版本 | V0.6（拉黑任务改为运营先确认，教师内容待配置） |
| 文档状态 / 业务口径 | 已确认 / 13 类教师端任务进入当前开发范围 |
| 最近更新 | 2026-07-24 |

本文件只覆盖教师可见的任务内容、触发事实和完成方式，不承接纯运营 Case、人工审核队列或运营看板。正式任务只能由世文任务触发中心根据真实业务数据从已发布模板中选择，并在共享 `task_assignments` 中创建 `PERSONALIZED_IMPROVEMENT + TRIGGER_CENTER` assignment。教师端不自行触发，也不默认向所有老师显示全部模板。

## 1. 已确认的教师可见任务

| 顺序 | 任务 ID | 模板 | 教师可见触发事实 | 完成方式 |
| --- | --- | --- | --- | --- |
| 1 | `classroom-quality-reminder` | `NT-Q03` assignment + 关联提醒 | 未开摄像头、CPU 占用过高或网络延迟过高 | **课中质量问题**：查看异常明细并完成配置的检查 |
| 2 | `attendance-reliability-refresher` | `P-REL-ATTENDANCE` | 普通缺席、迟到或早退 | **出席问题**：阔知规则培训与考试 |
| 3 | `lesson-memo-rules-learning` | `P-REL-MEMO` | 缺席原因明细为 `Unfilled Lesson Memo` | **出席（未填写 Lesson Memo）问题**：Lesson Memo 规范学习 |
| 4 | `feedback-self-study` | `P-FB-NEGATIVE / P-FB-COMPLAINT` + `LESSON_PACING_AND_FLOW` | 课堂节奏、未讲完教材或过早讲完 | **掌握课堂节奏** |
| 5 | `feedback-interaction-engagement` | 同上 + `INTERACTION_AND_ENGAGEMENT` | 只朗读／缺少互动 | **让学员更多开口** |
| 6 | `feedback-correction-explanation` | 同上 + `ERROR_CORRECTION_AND_EXPLANATION` | 无纠错／纠错不足 | **做清楚、友好的纠错** |
| 7 | `feedback-speaking-pace` | 同上 + `SPEAKING_PACE` | 语速过快／过慢 | **使用舒适的课堂语速** |
| 8 | `feedback-scaffolding-language` | 同上 + `SCAFFOLDING_LANGUAGE` | 授课用语过难／缺少引导 | **让授课语言更容易理解** |
| 9 | `feedback-teaching-aids` | 同上 + `TEACHING_AIDS` | 缺少教具／课堂不生动 | **让课堂内容更生动** |
| 10 | `feedback-student-response` | 同上 + `STUDENT_RESPONSE` | 未及时回应学员问题 | **及时回应学员** |
| 11 | `feedback-pronunciation` | 同上 + `PRONUNCIATION_AND_ACCENT` | 发音、单词发音不准确或有口音 | **清楚示范目标发音** |
| 12 | `feedback-professionalism` | 同上 + `PROFESSIONAL_ATTITUDE_AND_CONDUCT` | 职业态度／行为规范问题 | **保持专注和专业** |
| 13 | `feedback-blacklist-review` | `P-FB-BLACKLIST` | 世文检测不同学员拉黑同一教师达到 2 人及以上后，先推送运营确认；运营确认老师确有问题并选择教师任务后，才创建教师 assignment | 教师端任务内容与完成方式待嘉荷配置；不再要求老师填写拉黑原因或事实说明 |

触发条件、教师任务模板和任务实例必须分开：缺勤、迟到及配置中的早退是不同触发条件，但教师收到的是同一个 Reliability 复训模板；当前已有未完成实例时，新课程只追加为触发证据，不复制任务卡。Lesson Memo 原因使用另一种教学内容，因此单独显示。

`Reliability`、模板编号、触发规则等术语只用于内部路由。教师端统一使用动作型标题和用户语言，直接说明对应课程、发生了什么以及下一步做什么。

用户反馈按《个性化任务推送逻辑与任务清单》第 4.4 节的粒度拆任务，并结合世文字段表补充发音与职业规范，共九个教师可行动学习主题。差评和 L2–L4 一般投诉只是两种触发来源，不按来源复制学习页面。出席、网络设备和 Lesson Memo 分类分别回到现有 Reliability、课中质量或 Memo 任务。

## 2. 不进入教师端的保护规则

以下输入不得为了“让页面有内容”而生成教师任务：

| 输入 | 教师端预期 |
| --- | --- |
| L0–L1 投诉 | 不生成教师任务 |
| 投诉仍在核验或结论不成立 | 不生成教师任务 |
| 同类差评只有 1 条 | 不生成重复反馈自学任务 |
| 已经发生、无法再采取行动的课堂质量结果 | 不生成提醒任务 |
| 同一天多节可处理的设备／网络／音频异常 | 合并成 1 条提醒，不重复生成 |
| 已存在未完成的 Reliability 复训，又出现新的真实缺勤／迟到 | 追加触发课程，不重复生成复训卡 |

缺勤、迟到、早退属于已经确认的出勤结果时，可以触发后续阔知学习和考试，但不把已经发生的事件包装成“可逆提醒”。

## 3. 教师可见证据字段

每个真实 assignment 至少展示：

- 课程时间与“课程编号”；
- 教师可理解的结果，例如迟到时长、缺勤、早退时长、反馈主题或设备异常；
- 对应的学习／检测动作。

`teacher_id`、`appoint_id` 等原始字段只保留在数据源中；教师端不直接展示字段名，也不展示学员身份、投诉原文、运营内部备注、内部风险标签和未确认结论。

世文在 `task_assignments` 创建任务，教师端从 `evidence_snapshot.lesson_ids / signal_samples[].why` 生成教师安全的关联课程和触发事实，不透传内部 evidence。需要提醒时，世文在 `public.notifications` 创建通过 `task_id` 关联同一 assignment 的提醒；教师端只允许提醒补充 `payload.teacher_safe_facts` 和 `payload.related_courses` 白名单字段。

## 4. 完成与关闭

- Reliability：在阔知完成配置的培训和考试，全部必修任务 `percent=100`；TIDE 不保存题目、答案或作答。
- Lesson Memo：完成嘉荷配置的规则与时效学习。
- 教学反馈：完成与教师可见反馈主题匹配的版本化学习章节。
- 设备与连接检查：完成配置的检查并记录是否通过；未通过时保持可继续处理的状态。
- 拉黑问题：先由运营确认是否需要推送教师任务；确认并选定任务后才创建教师 assignment，具体内容与完成方式待嘉荷配置。
- 个性化改善任务不计新师任务积分。

## 5. 验收

| 编号 | 验收场景 | 预期结果 |
| --- | --- | --- |
| AC-01 | 当前老师没有个性化 assignment | Tasks 和 My TIDE 不使用前端模板伪造任务入口 |
| AC-02 | 任务触发中心为老师创建一条已发布模板的 assignment | 教师端展示同一任务实例、教师安全原因和必要关联课程 |
| AC-03 | 打开 Reliability 复训 | 在同一任务详情中展示已合并的迟到与真实缺勤课程，并进入统一阔知培训与考试流程 |
| AC-04 | 打开 Lesson Memo 实例 | 只进入 Memo 规则学习，不错误分配完整 Reliability 复训 |
| AC-05 | 已有未完成的同类任务又出现新证据 | 按配置追加或合并证据，不创建重复卡片 |
| AC-06 | 打开已确认的反馈改善实例 | 只展示教师可见分类、必要关联课程和匹配学习，不展示学员身份、投诉原文或内部风险标签 |
| AC-07 | 检查教师任务集合 | 不包含 L0–L1 投诉和不可逆提醒；拉黑量达到 2 人及以上只进入运营确认，不直接创建教师任务 |
| AC-08 | 完成个性化任务 | 共享 assignment 按状态机完成，不产生 `FIXED_TASK_AWARD` |
| AC-09 | 运营确认拉黑问题并选择教师任务 | 仅此时创建教师 assignment；教师端不展示填写拉黑原因或事实说明的入口，具体内容待嘉荷配置 |
| AC-09 | 切换中英文 | 标题、原因、步骤、完成标准与触发事实均可理解 |

## 6. 后端接入待补

- 将现有 13 类前端模板迁移为共享 `task_templates` 中的版本化已发布模板；
- 任务触发中心使用真实课程级数据直接在共享 `task_assignments` 创建、去重、合并、撤回或再次触发实例；不恢复任务 HTTP 下发／回传副本；
- 教师端任务查询、详情、进度和状态更新支持当前教师的 `PERSONALIZED_IMPROVEMENT` assignment；
- Reliability 正式阔知课程、视频与考试任务 ID；
- Lesson Memo、重复反馈和投诉改善的正式内容配置；涉及考试时必须使用阔知；
- 视频受控存储地址与素材版本；
- 真实触发样本的正向、边界和不创建用例联调。
