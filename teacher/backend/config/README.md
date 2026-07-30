# 系统通知配置

一期不建设运营后台。项目组在 `system-notifications.json` 中配置英文纯文本通知，后端按北京时间定时发布。

## 规则

- `configKey` 发布后不可复用或修改；内容错误时将原配置设为 `cancelled: true`，再使用新的 `configKey` 发布。
- `publishAt / expiresAt` 必须显式使用 `+08:00`。
- 接收名单在实际发布时间生成，之后进入范围的老师不补发。
- `audience` 可以使用 `all`，或使用教师 ID、导入批次、训练营天数、账号状态、任务及任务状态组合筛选；多个条件之间为“并且”。
- 每条通知最多一个系统内操作。按钮文案由前端按操作类型固定展示。
- 文案先说明对老师的影响，再给出清楚、温和的下一步；不使用内部测试、风险标签、错误码或归责式表达。
- 维护、故障、恢复、功能更新和规则更新走本配置发布。任务结果、个性化任务、成长阶段开放和密码变更由对应业务事件自动生成，不在这里重复配置。
- 固定任务编码调整时，禁止按 code 机械替换已有受众或 `TASK_DETAIL` 操作。G02–G09 在新旧目录中编码重叠，必须按实际任务语义人工复核并使用新的 `configKey` 重建尚未发布的配置；迁移 `0025_fixed_task_semantic_alignment` 会拒绝仍引用 G00／G10 的 `SCHEDULED` 记录。

## 示例

```json
{
  "version": 1,
  "publications": [
    {
      "configKey": "maintenance:2026-08-01",
      "typeCode": "SYSTEM_MAINTENANCE",
      "title": "Scheduled maintenance",
      "body": "TIDE will be unavailable from 09:00 to 09:30. Your saved task progress will not be affected.",
      "publishAt": "2026-08-01T08:30:00+08:00",
      "expiresAt": "2026-08-01T09:30:00+08:00",
      "audience": { "all": true }
    }
  ]
}
```

## 推荐文案

| 类型 | 标题 | 正文模板 | 建议操作 |
|---|---|---|---|
| `SYSTEM_MAINTENANCE` | `Scheduled maintenance` | `TIDE will be unavailable from {startTime} to {endTime}. Your saved task progress will not be affected.` | 无 |
| `SERVICE_INCIDENT` | `Some TIDE features are temporarily unavailable` | `We’re working to restore the affected features. Your saved task progress will not be affected.` | 无 |
| `SERVICE_RESTORED` | `TIDE is available again` | `The affected features are available again. You can continue from where you left off.` | `MY_TIDE` |
| `FEATURE_UPDATE` | `A new TIDE feature is available` | `{featureSummary}. You can explore it now or continue using TIDE as usual.` | 按内容选择 |
| `POLICY_UPDATE` | `An important training update` | `{updateSummary}. Please review the update before continuing the related task.` | `HELP` 或 `TASK_DETAIL` |

花括号内容只是编辑提示，发布前必须替换为具体英文正文。
