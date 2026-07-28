# 配置中心运行契约

状态：唯一当前契约。运营页只展示当前生效配置和当前待发布草稿，不提供历史版本列表。2026-07-28 已将测试库中的旧积分配置历史收敛删除，修改后的完整积分规则是唯一 `v1`；其他配置域仍按各自审计链管理。

## 边界

配置中心只管理三个受控配置域，不承载自由 JSON 配置，也不允许 Agent 自由发明任务：

| 配置域 | 运行影响 | 开发默认 |
|---|---|---|
| `SCORE_GRADUATION` | 五维计分、raw / 对外显示、出营与金牌硬门槛 | 当前发布规则见《数据与积分规则》 |
| `AGENT_POLICY` | Agent 启停、熔断、主次任务上限、provider/model | enabled；kill switch off；主 `1`、次 `2`；`openai/gpt-5.6-terra`；禁止自由发明 |
| `DELIVERY_POLICY` | 普通/紧急提醒与 P0 回复、提醒时限 | 普通 `60m`、紧急 `15m`、P0 回复 `120m`、P0 提醒 `30m` |

当前发布的积分配置为 `SCORE_GRADUATION.policy_version = v1`，不使用 cap、权重、负向扣分或五维最低线：

- 用户反馈和可靠性按最新有效的 `teacher_metric_snapshots` 教师维度统计快照计算：好评 `+5`、收藏 `+5`、完美完课 `+4`、Peak 完课 `+2`；15 日复约字段和 `on_time_completed_cnt` 继续保存但不计分；
- 课堂质量当前无加分项，维度分为 0，来源状态为 `NOT_APPLICABLE`；
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

服务端仍可解析旧格式 `v2`–`v10`，仅用于回读旧快照。旧格式中的复约积分、
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
- `v1` 草稿允许运营调整各计分单价、供给阈值/分值、raw 出营与金牌分数线、L0
  上限及金牌迟到/早退/缺席上限。任务项数量与任务总分、指标标识、结算方式、
  对外显示锚点和“金牌继承出营”属于跨端结构契约，页面锁定且服务端拒绝改写。
- 草稿创建和更新也会先执行完整白名单 Schema 校验；未知字段、错误类型和疑似凭据不会先落库再等待后续校验。
- 三个配置域均按高影响配置管理：草稿创建人与发布人必须是不同账号。
- actor 只来自服务端验证过的运营会话；请求体出现额外 `actor_id` 会被拒绝。
- 替代配置发布时，原 `PUBLISHED` 记录转为 `RETIRED`，payload 不覆盖。
- `SCORE_GRADUATION` 发布会在同一事务内全量重算当前教师投影；重算失败时，新版本发布和旧版本退役同时回滚。发布接口成功返回时重算已经完成，并返回 `recalculation` 摘要。
- 积分规则重算只改变当前分数和当前门槛命中情况；已经获得的出营、金牌资格不可撤销。
- 已发布运行配置不能被单独退役；停用 Agent 必须发布 `kill_switch=true` 的替代版本，不能让运行时悄悄退回环境变量或代码默认值。
- 每次创建、更新、校验失败、校验通过、发布和退役都有独立审计记录和 payload hash。
- `AGENT_POLICY.kill_switch=true` 无条件覆盖 `enabled=true`。

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

运行服务通过 `app.config_service.get_published_payload(key)` 读取唯一已发布 payload。若返回 `None`，调用方必须采用代码内安全降级并显式暴露“未配置”，不能隐式写库。
