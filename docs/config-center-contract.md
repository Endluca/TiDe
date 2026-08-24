# 配置中心运行契约

状态：唯一当前契约。运营页只展示当前生效配置和当前待发布草稿，不提供历史版本列表。2026-07-28 已将测试库中的旧积分配置历史收敛删除，修改后的完整积分规则是唯一 `v1`；其他配置域仍按各自审计链管理。

## 边界

配置中心只管理四个受控配置域，不承载自由 JSON 配置，也不允许 Agent 自由发明任务：

| 配置域 | 运行影响 | 开发默认 |
|---|---|---|
| `SCORE_GRADUATION` | 五维计分、raw / 对外显示、出营与金牌硬门槛 | 当前发布规则见《数据与积分规则》 |
| `AGENT_POLICY` | Agent 启停、熔断、主次任务上限、provider/model | enabled；kill switch off；主 `1`、次 `2`；`openai/gpt-5.6-terra`；禁止自由发明 |
| `DELIVERY_POLICY` | 普通/紧急提醒与 P0 回复、提醒时限 | 普通 `60m`、紧急 `15m`、P0 回复 `120m`、P0 提醒 `30m` |
| `teacher_personalized_copy` | 个性化任务英文标题、负面标签英文映射与执行变体 | 唯一 `cfg-teacher-personalized-copy-v1`；完整 schema 与 Seed 见下文 |

`teacher_personalized_copy` 的物理 key 精确使用上述小写值，不能写成大写别名。首个版本固定
`version_id=cfg-teacher-personalized-copy-v1,version_number=1,schema_version=1,high_impact=true`。v1 payload
必须满足 `additionalProperties=false` 的下列精确结构；所有列出的 key 均 required，映射按源值精确匹配，
不 trim、不模糊匹配：

```json
{
  "complaint_title_prefix": "General Complaint - ",
  "negative_title_prefix": "Negative Feedback - ",
  "fallback_titles": {
    "complaint": "General Complaint - Complaint Category",
    "negative": "Negative Feedback - Feedback Pattern"
  },
  "complaint_category_to_en": {
    "未及时回应学员问题": "Did Not Respond to the Student Promptly",
    "过早上完教材,等待下课": "Finished Courseware Too Early and Waited for Class to End",
    "外教向学员借钱": "Teacher Asked Student for Money",
    "迟到": "Late Arrival",
    "网络卡顿": "Unstable Network",
    "麦克风没有声音/卡顿": "Microphone Audio Missing or Unstable",
    "语速过快": "Speaking Too Fast"
  },
  "negative_label_to_en": {
    "上课死板": "Rigid Teaching Style",
    "不够耐心": "Insufficient Patience",
    "发音不准": "Inaccurate Pronunciation",
    "只是读课件": "Only Reading the Courseware",
    "很少鼓励孩子": "Insufficient Student Encouragement",
    "教的太难": "Content Too Difficult",
    "有口音听不懂": "Accent Difficult to Understand",
    "有噪音/老师声音小": "Background Noise or Low Teacher Volume",
    "未讲完教材": "Courseware Not Completed",
    "灯光过暗/亮": "Lighting Too Dark or Too Bright",
    "环境乱/灯光差": "Distracting Environment or Poor Lighting",
    "缺乏热情": "Lack of Enthusiasm",
    "缺乏耐心": "Lack of Patience",
    "缺少互动": "Insufficient Interaction",
    "网络设备差": "Poor Network or Equipment",
    "老师上课不专注": "Teacher Not Focused",
    "语速太快": "Speaking Too Fast",
    "语速过快": "Speaking Too Fast"
  },
  "negative_label_execution_variant": {
    "灯光过暗/亮": "TEACHING_ENVIRONMENT_PHOTO",
    "环境乱/灯光差": "TEACHING_ENVIRONMENT_PHOTO"
  },
  "default_negative_execution_variant": "GENERAL"
}
```

字符串必须非空且无控制字符；variant 只允许 `GENERAL/TEACHING_ENVIRONMENT_PHOTO`，照片 variant 的 key
必须同时存在于 `negative_label_to_en`，v1 精确要求只有上面两枚照片 key。标题只能由 prefix+命中英文或完整
fallback 构造。payload hash 使用 UTF-8、递归按 key bytes排序、无多余空白的 canonical JSON SHA-256；Seed
脚本、发布审计、TASK_PLAN 与 assignment 都使用同一数据库 canonical hash函数，禁止运行时回读 Python
常量形成第二真相。

当前发布的积分配置为 `SCORE_GRADUATION.policy_version = v1`，不使用 cap、权重、负向扣分或五维最低线：

- 用户反馈的好评和可靠性的 Peak 完课读取 `teacher_source_wide` 当前计数：好评 `+5`、Peak 完课 `+2`；收藏 `+5` 改为读取课程 end+24h 锁定、同师生只归因一课的收藏事实，不再直接读取当前关系人数；完美完课由 `lesson_source_wide` 的课程状态、迟到和早退逐课派生，每课 `+4`；15 日复约字段只保存、不计分；
- 课堂质量按 `lesson_source_wide` 逐课计算：未开摄像头、CPU 占用过高、网络延迟过高三个字段均明确为 0 时加 2 分；任一字段为 1 或为空均不加分，空值标记为 `SOURCE_MISSING`。CPU、网络旧来源已经取消，替换来源接入前两字段必须为 `NULL`，因此过渡期不得新增硬件质量分；
- 当前 9 项固定成长任务合计最多 30 分。每个合法 `COMPLETED` assignment 按其 `template_version_id` 固定引用的 `task_templates.payload.score_value` 累加，不等待其余任务完成；
- 唯一供给积分规则为 `peak_slot_cnt >= 40`，首次达成时自动加 10 分并永久锁定；后续字段下降或数据纠错都不撤分，纠错只保留前后值和审计；
- 当前 5 类个性化改善任务均为 0 分，其完成状态不产生积分流水；
- 出营资格只含三个条件：9 个固定成长任务全部完成、L0 投诉次数等于 0、raw 总分不低于 100；
- 金牌资格有五个条件：继承出营资格、raw 总分不低于 200、`late_cnt <= 1`、
  `early_cnt = 0`、`absent_cnt = 0`；`graduation_effect` 继续为
  `IMMEDIATE_ON_CRITERIA`；显示锚点仍为 `100 / 200 / 100 / 200`。

v1 中三个出营条件分别由以下受控配置表达：

| 条件 | 配置字段 | 当前值 |
|---|---|---:|
| 固定成长任务全部完成 | `hard_gates.graduation.required_mandatory_task_count` | `9` |
| L0 投诉为 0 | `hard_gates.graduation.maximum_l0_complaint_count` | `0` |
| raw 总分达标 | `thresholds.graduation_raw_score` | `100` |

金牌条件由以下受控配置表达：

| 条件 | 配置字段 | 当前值 |
|---|---|---:|
| 继承出营资格 | `hard_gates.gold.inherits_graduation` | `true` |
| raw 总分达标 | `thresholds.gold_raw_score` | `200` |
| 迟到次数 | `hard_gates.gold.maximum_late_count` | `1` |
| 早退次数 | `hard_gates.gold.maximum_early_count` | `0` |
| 缺席次数 | `hard_gates.gold.maximum_absent_count` | `0` |

完整业务口径只在 [数据与积分规则](数据与积分规则.md) 维护。未来任何语义变化必须创建新的不可变配置记录并重新回测，但当前文档只描述生效规则。

## 历史配置兼容

服务端仍可解析旧格式 `v2`–`v10`，仅用于兼容历史配置内容。旧格式中的复约积分、
`class_quality_no_issue_rate`、80% 模拟达成率、`severe_redline_event` 及旧出营门槛
和旧金牌门槛均是历史语义；它们不能作为新草稿的当前依据，也不参与 v1 的运行时资格投影。配置中心数据库本身只保留唯一 v1 积分配置。

空库读取返回未配置，不会由 API 自动创建默认值。初始化环境时显式执行：

```bash
cd backend
.venv/bin/python scripts/seed_config_center.py
```

## 状态与发布治理

```text
DRAFT -> VALIDATED -> PUBLISHED
                         └── 替代配置发布时，原生效记录转为 RETIRED
```

- 只有 `DRAFT` 可编辑；校验后若需修改，必须另建草稿。
- `v1` 草稿允许运营调整可逆课程计分单价、raw 出营与金牌分数线、L0
  上限及金牌迟到/早退/缺席上限。供给里程碑的 `CAPACITY_PEAK_SLOT_40` 身份、阈值40和分值10，
  以及任务项数量、G01–G09逐项分值、五个P任务0分、任务总分、指标标识、结算方式、
  对外显示锚点和“金牌继承出营”属于跨端结构契约，页面锁定且服务端拒绝改写。
- 草稿创建和更新也会先执行完整白名单 Schema 校验；未知字段、错误类型和疑似凭据不会先落库再等待后续校验。
- 四个配置域均按高影响配置管理：草稿创建人与发布人必须是不同账号。
- actor 只来自服务端验证过的运营会话；请求体出现额外 `actor_id` 会被拒绝。
- 替代配置发布时，原 `PUBLISHED` 记录转为 `RETIRED`，payload 不覆盖。
- `SCORE_GRADUATION` 发布取得该配置域 exclusive catalog lock，并在同一事务内全量重算当前教师投影；
  SourceWide/Fixed Task Settler/V2_PRIMARY Correction Service及任何共用rebuild命令从读规则到提交持 shared lock，且在账户锁后校验 version_id/payload_hash/rule
  version。重算失败时新版本发布和旧版本退役同时回滚，旧规则 Worker 也不能晚提交覆盖新结果。
- 可逆课程单价变化必须走组件生命周期：非收藏组件及收藏归因都先唯一冲正旧奖励、递增generation，再按
  新版本奖励；收藏held状态不因换版解除，REVERSED不重开。
- 积分规则重算只改变当前分数和当前门槛命中情况；已经获得的出营、金牌资格不可撤销。
- 已发布运行配置不能被单独退役；停用 Agent 必须发布 `kill_switch=true` 的替代版本，不能让运行时悄悄退回环境变量或代码默认值。
- 每次创建、更新、校验失败、校验通过、发布和退役都有独立审计记录和 payload hash。
- `AGENT_POLICY.kill_switch=true` 无条件覆盖 `enabled=true`。
- `teacher_personalized_copy` 发布必须取得 cutover shared mutex 与 teacher-copy catalog exclusive lock，在同一
  事务执行旧 PUBLISHED→RETIRED、新 VALIDATED→PUBLISHED、audit，并对全部有 active match且尚无 assignment
  的 TASK_PLAN key做 canonical fan-out；fan-out前先按新copy重评未物化match的标题、variant、evidence/hash及
  negative name/variant blocker，match语义变化递增revision，每key最终只产生一个plan revision。既有assignment
  不改。match/Planner读取时取同 catalog shared lock。PUBLISHED/RETIRED 的
  identity/schema/payload/hash不可改，RETIRED终态，不能单独退役或复活。受限发布函数以外的 status写入拒绝。
- 该 key 缺失、重复或 schema/hash非法时禁止代码 fallback：对应 TASK_PLAN 固定
  `materializable=false,blocker_code=TASK_COPY_CONFIG_MISSING|TASK_COPY_CONFIG_CONFLICT`，不建 assignment；
  健康检查、final shadow与 cutover失败关闭。合法版本发布的同事务 fan-out生成新 revision并解除 blocker。

## API

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/configs?key=&status=` | 服务端审计/管理查询；运营页不展示历史列表 |
| GET | `/api/configs/published/{config_key}` | 当前已发布 payload |
| POST | `/api/configs/drafts` | 从当前发布版本或指定版本创建草稿 |
| PATCH | `/api/configs/{version_id}` | 更新草稿 |
| POST | `/api/configs/{version_id}/validate` | 服务端业务校验 |
| POST | `/api/configs/{version_id}/publish` | 双人发布 |
| POST | `/api/configs/{version_id}/retire` | 受保护端点；拒绝单独移除已发布配置，退役只随替代版本发布发生 |
| GET | `/api/configs/{version_id}/audits` | 配置记录审计链 |

运行服务通过 `app.config_service.get_published_payload(key)` 读取唯一已发布 payload。除
`teacher_personalized_copy` 外，若返回 `None`，调用方可按各域已冻结的安全降级显式暴露“未配置”，不能隐式
写库；teacher copy 必须按上文失败关闭，绝不使用代码默认文案或 variant。
