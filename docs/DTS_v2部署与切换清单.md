# DTS v2 部署与切换清单

本文只描述发布执行边界。业务规则以
[`DTS_direct开发冻结实施规格.md`](DTS_direct开发冻结实施规格.md) 为准。

## 1. 版本目标

- public Alembic head：`20260823_100_scope_snapshot_diff`
- teacher migration head：`0043_p_rel_execution_catalog`
- teacher canonical migration：精确 38 条
- 稳定读取路由：由数据库 `dts_projection_read_routes` 决定，不由 Pod 环境变量自行决定

`V1` 不是第二套长期业务版本。它只用于升级前运行；迁移后使用以下三态：

| 数据库模式 | V1 兼容投影 | V2 Domain | V2 业务物化 | 用途 |
|---|---|---|---|---|
| `V1_COMPAT_DUAL_CAPTURE` | 开 | 开 | 关 | 双写追平和对账 |
| `V2_PRIMARY` | 关 | 开 | 开 | 正式主链路 |
| `ROLLED_BACK` | 开 | 开 | 关 | 紧急回退到兼容读取 |

两个消费通道使用独立队列和进度。任何通道都不能替另一通道确认已消费。

## 2. 切换前硬门槛

缺少任一项都停在 `V1_COMPAT_DUAL_CAPTURE`，不得切 `V2_PRIMARY`：

1. 已审核的地区/表级 source profile 已进入发布镜像，manifest SHA-256 与数据库批准记录一致；仓库当前空 manifest 不能用于生产。
2. DOM/OVS 真实消费组、topic、broker epoch、H0 vector 和 checkpoint 已现场读回。
3. CURRENT scope snapshot 有权威导出、源事务一致性 token、文件 SHA-256、profile SHA-256 和 fence 证明；空文件或 `SOURCE_MISSING` 不能冒充 `COMPLETE`。
4. V1/V2 队列无死信、无过期租约，双流已追平同一 fence。
5. 14 类 reconciliation 结果全部通过：教师宽表、逐课分、逐课组件、收藏观察、收藏归因、触发、任务、Case、提醒、账户、组件账户、积分流水计划、资格和 Outbox 覆盖。
6. 课程 595 的考试 `courseTaskId/paperId` 未提供时，`P-REL-ATTENDANCE` 只能进入课程并展示视频进度，必须保持 `completionEnabled=false`，不能验收自动完成。

## 3. 发布顺序

1. 固定待发布 commit/image digest，备份数据库，确认维护窗口。停止旧 SourceWide、旧 DTS 消费者和积分写事务，确认没有在途事务后再迁移。
2. 使用 `tide_sys_admin` 的一次性迁移连接执行 public Alembic 到 head；读回必须精确为 `20260823_100_scope_snapshot_diff`。
3. 在 public 已到 head 后执行 teacher 迁移到 `0043_p_rel_execution_catalog`；读回精确 38 条账本，并执行 teacher `verify.sh`。
4. 使用真实 DOM/OVS epoch 和 H0 vector 调用 rev79 受限 bootstrap；读回 control 初态必须为 `V1_COMPAT_DUAL_CAPTURE`。不得手工 `INSERT/UPDATE dts_pipeline_control`。
5. 部署同一版本镜像：
   - 两个 DTS 项目均设 `TIT_DTS_PIPELINE_MODE=V1_COMPAT_DUAL_CAPTURE`；
   - legacy projection 保持开启；
   - application 设 `TIT_V2_RUNTIME_ENABLED=true`；
   - V2 Domain 追数，V2 Outbox/Favorite 保持非 PRIMARY standby。
6. 分别读回两个 DTS heartbeat/checkpoint、application 三组 V2 health、V1/V2 dirty queue 和 source profile identity。配置声明不能替代数据库读回。
7. 用受限 scope coordinator 先 `validate`、再 `dry-run`，确认 hash、角色、目标 head、scope 和当前态一致后才 `apply`；最后 `readback`。命令入口：

   ```bash
   cd backend
   .venv/bin/python scripts/run_dts_source_scope_snapshot.py validate \
     --candidate /受控路径/snapshot.jsonl \
     --profile-manifest /受控路径/dts_source_profiles_v2.json \
     --expected-artifact-sha256 64位小写SHA256 \
     --expected-profile-manifest-sha256 64位小写SHA256

   .venv/bin/python scripts/run_dts_source_scope_snapshot.py dry-run \
     --candidate /受控路径/snapshot.jsonl \
     --profile-manifest /受控路径/dts_source_profiles_v2.json \
     --expected-artifact-sha256 64位小写SHA256 \
     --expected-profile-manifest-sha256 64位小写SHA256
   ```

   `dry-run` 不写库；`apply` 使用相同四项证据。完整变量和读回格式见
   [`DTS_source_scope_snapshot协调器.md`](DTS_source_scope_snapshot协调器.md)。
8. 固定同一个 `evaluation_as_of`，生成 V1/V2 14 类结果清单和技术门禁清单；使用专用 `tit_dts_projection_cutover_runtime` 角色记录 reconciliation PASS。运行 ID、manifest、fence 或 control/route version 任一变化都必须重新生成，不复用旧 PASS。
9. 最终切换使用维护窗口：暂停两条 DTS 写入和 application 后台 Worker；调用 `switch_dts_projection_mode_v2(..., 'V2_PRIMARY')` 原子更新数据库 control 与稳定读取路由；随后把两个 DTS 项目改为 `V2_PRIMARY`、关闭 legacy projection，再启动同版本 application。不能先滚一个 Pod 再等待另一套状态追上。
10. 读回 `dts_projection_readiness_v1()`、两个外部 API、三组 V2 health、稳定教师积分/课程视图和 14 类抽样。只有这些证据通过后，才能单独评审并打开不可逆资格授予门禁。

## 4. 回滚

1. 先关闭不可逆资格授予门禁并暂停写入。
2. 使用上次成功切换留下的 control/route version 和受限 cutover 角色，调用 `switch_dts_projection_mode_v2(..., 'ROLLED_BACK')`。
3. 两个 DTS 项目改为 `ROLLED_BACK`，重新开启 legacy projection；application V2 Domain 继续追数，Outbox/Favorite 停止物化。
4. 读回稳定视图已回到 `V1_COMPAT`，再恢复流量。

回滚不撤销已经合法授予的出营或金牌资格；发现错误资格时走审计后的业务补偿，不直接删事实。

## 5. 发布完成证据

- public/teacher 两条迁移 head 和账本读回；
- 两个 Gaea 项目的地区、image digest、pipeline mode、checkpoint 和 heartbeat；
- source profile approval、scope snapshot、fence 与 H0 bootstrap 审计；
- reconciliation PASS、switch audit、readiness；
- 队列/死信/租约统计和逐类业务抽样；
- 教师总分展示上限 200、实际分继续累计、出营/金牌时间与不可逆资格读回；
- 代课、评价、标签、缺席、收藏 24 小时归因、拉黑、投诉、TESOL、DOM 映射的回放样本。

代码、迁移、测试或 Pod 健康本身都不是“已上线”证明。
