# DTS 单通道正式发布清单

目标：删除系统中全部历史消费事实，DOM、OVS 从同一个明确的新时间点开始，只消费该时间点之后的新事件。没有 V1、双写、追平、旧数据回填或 V1/V2 对账。

## 1. 固定版本与规则

- public head：`20260824_101_dts_single_pipeline_reset`
- teacher head：`0043_p_rel_execution_catalog`
- 唯一外部运行模式：`SINGLE_PIPELINE`
- 所有业务源表：无当前行且无法组成完整当前行的 UPDATE/DELETE 记录 `SOURCE_CHANGE_WITHOUT_CURRENT_IGNORED`、推进 checkpoint，不创建 source current/version 或 dirty key；后续 INSERT 建立基线后再正常处理。`dom_appoint/ovs_appoint` 无基线 UPDATE 始终按此规则忽略
- rev101 是破坏性迁移：清空课程、教师投影、参与记录、积分、资格、关系、任务实例、通知/输出、DTS ledger/checkpoint/current/version/dirty/scope 等消费事实；保留任务目录、积分规则和版本化配置

## 2. 发布前

1. 固定一个带时区的新消费时间，例如 `2026-08-25T00:00:00+08:00`。DOM、OVS 使用完全相同的值。
2. 固定待发布 commit 和三个镜像。
3. 备份正式数据库并验证备份可读。
4. 停止旧 DOM、OVS 和 application，确认数据库中没有相关运行角色的在途事务。
5. 保存旧 DTS consumer group、topic 和订阅配置；新发布继续使用真实订阅，但每个地区配置一个新的、互不相同的 `TIT_DTS_SOURCE_PARTITION_EPOCH_ID`。

## 3. 执行顺序

### 3.1 数据库迁移

以迁移角色执行 public Alembic 到 head；teacher 未到 0043 时再执行 teacher 迁移。读回：

```sql
SELECT version_num FROM public.alembic_version;
SELECT migration_id, migration_order, filename, sha256, applied_at
FROM tide.schema_migrations
ORDER BY migration_order;
SELECT * FROM public.dts_projection_readiness_v1();
```

必须满足：public head 正确、teacher canonical 账本正确、readiness 为 `READY_SINGLE_PIPELINE`。同时确认 reset audit 恰有本次记录，旧 checkpoint、ledger、source current/version 和业务事实表为空。

### 3.2 发布 DOM

关键配置：

```dotenv
TIT_DTS_PIPELINE_MODE=SINGLE_PIPELINE
TIT_DTS_START_AT=2026-08-25T00:00:00+08:00
TIT_DTS_SOURCE_PARTITION_EPOCH_ID=<本次 DOM 新值>
```

启动后必须看到首条新事件建立 DOM checkpoint；checkpoint 的初始 offset 和时间来自真实事件，不由迁移伪造。核对 ledger 持续推进且没有 profile、权限或 schema 错误后再继续。

### 3.3 发布 OVS

配置与 DOM 相同，但 epoch id 使用本次 OVS 新值。启动后按同样方式确认 OVS checkpoint 和 ledger 推进。

### 3.4 发布 application

DOM、OVS 都已产生新 checkpoint 后发布 application，启用现行 Domain/Outbox/Favorite 等消费进程。首次发布保持不可逆出营/金牌授予门禁关闭；抽样确认课程、代课、评价标签、缺席、收藏 24 小时归因、拉黑、投诉、TESOL 和积分正确后，再用受限命令打开门禁，并让 application 的资格门禁配置与数据库一致。

## 4. 上线验收

- DOM/OVS：heartbeat、checkpoint、epoch、消费延迟正常；无旧时间点事件落库
- 缺失课程的 appoint UPDATE：ledger 为 `IGNORED`，无课程/source current/dirty key
- appoint INSERT 后 t_id 更新：旧教师参与记录变缺席，新教师新增参与记录；首次进入 end 的教师冻结为完课教师
- 评价、标签、缺席原因及任务、收藏/拉黑、投诉、TESOL、出营/金牌与确认文档一致
- dirty、Domain、Outbox、Favorite 无过期租约和死信；课程和积分读取稳定

## 5. 异常处理

没有 V1 回退通道。异常时停止 DOM、OVS 和 application：

- 代码或配置问题：修复后从数据库 checkpoint 继续
- 需要重新选择消费日期：恢复发布前备份，重新执行 reset 流程；不得直接手改 checkpoint
- 已消费数据发生业务错误：停止发布并纠正代码，不用旧 V1 数据补写

正式环境迁移、发布和最终读回由发布人执行；代码测试通过不等于正式环境已上线。
