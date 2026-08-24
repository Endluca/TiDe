# DTS direct 手册审查与累计修改项清单

> 文档性质：实现前审查和开发输入，不是发布完成证明
> 主手册：`docs/DTS_direct事件人工验证与纠错手册.md`
> 原始审查基线：`flow/release@436127d423966bd5077af5ad947956510bd00c30`
> 本次本地复审基线：`release@f6bb8ef49da1fe7525a2fb63e5674c9bd5af95ea`
> 复审日期：2026-08-21
> 范围：只复审文档和实现边界；本次没有修改业务代码、数据库迁移或测试。
> 后续实施（2026-08-24）：业务代码、rev101 清库迁移和测试已落地；当前发布架构为单通道、清空历史、从新时间消费，详见 `DTS_v2部署与切换清单.md`。本清单中的 V1/V2 切换项仅保留为历史决策记录。

## 1. 复审结论

主手册的总体结构合理，可以继续作为“人工构造 DTS 事件→推导代码行为→核对数据库结果”的验收手册。它的有效性主要来自四点：

1. 明确分开“现行代码行为”和“已确版目标口径”；
2. 事件处理有 `processed / ignored / duplicate / failed`、checkpoint、事务回滚和 Outbox 证据；
3. 代课、缺席、评价、关系事实和资格状态都写了来源与判断依据；
4. 没有把“DTS 消费成功”写成“业务计分和外部动作已完成”。

第二轮审查已把原 P0/P1 的实现级空白全部收敛到
`docs/DTS_direct开发冻结实施规格.md`：物理表、来源版本历史、地区化脏键、参与状态机、集合选择器、
完课纠错、聚合、排课、任务纠错、OVS 边界和切换顺序均有唯一答案。文档现在可以直接用于拆分和
实现代码；它仍不是迁移执行、DTS 消费、发布或业务验收完成证明。

## 2. 从初稿到当前版的累计修改项

下表记录主手册初次生成后到当前冻结版的变化。“最终口径”是后续实现和测试依据；本轮不再保留
需要开发者自行选择的“待确版”业务分支，明确排除在本轮范围外的功能不得擅自实现。

| 编号 | 主题 | 最终口径 | 直接影响 |
|---|---|---|---|
| M01 | 完课教师 | 课程首次进入 `end` 时冻结当时的 `t_id` 为实际完课教师；后续再改 `t_id` 不转移已冻结完课归属 | 需持久化首次 `end` 快照和冻结标记 |
| M02 | 代课识别 | `dom_appoint.t_id` 从 A 改为 B 就是代课；不等缺席原因事件 | A 参与行立即转 `t_absent`，B 新增参与行 |
| M03 | 缺席判定 | 缺席原因晚到只补充原因和决定动作，不再决定旧教师是否缺席 | 代课与缺席原因顺序必须可交换 |
| M04 | 缺席定位 | 缺席原因用 `appoint_id + t_id` 定位教师参与行；同键多条按 `add_time,id` 取最新，删除后重算剩余记录 | 不能只按 `appoint_id` 更新当前教师行 |
| M05 | 课程子事件 | 评价、投诉、标签、处罚等只要有 `appoint_id` 就记在该源课程，不要求课程已 `end`；有明确 `t_id` 时另保留教师指向 | 需区分源课程事实和教师参与事实 |
| M06 | 课程 `use_point` | 取消 `dom_appoint/ovs_appoint.use_point='buy'` 的课程准入限制 | `buy/free/空/其他` 都不能单独导致课程事实丢失 |
| M07 | 课程 `status` | 完全取消 `status NOT IN ('cancel','on')` 的课程准入限制；所有状态和 `NULL` 均先保留事实 | 课程入库与指标是否计数分开判断 |
| M08 | 课程状态文案 | 手册中完课前状态从错误的 `waiting` 统一修正为源值 `on` | 用例和 fixture 不得再发明 `waiting` |
| M09 | DOM 评价好差评 | `dom_user_teacher_grading.use_point='buy'` 时读 `score`：1/2 差评，4/5 好评；`free` 时读 `type`：`satisfactory` 好评，`unsatisfactory` 差评 | 需补充白名单字段和分支测试 |
| M10 | 未知评价分支 | `grading.use_point` 空或不是 `buy/free` 时忽略该评价，不猜测、不报错 | 不产生好/差评事实 |
| M11 | 评价标签 | `dom_grading_label_log` 保留 `appoint_id + label_id + label_name`，取消 `type=1` 和 `status='normal'` 限制 | 需标签明细事实，不能只存名称数组 |
| M12 | 国内标识 | 目标代码、配置、数据和测试统一使用 `dom`；`dmo` 只能描述待迁移现状 | 需同时做代码替换、存量数据迁移和下游对账 |
| M13 | 教师中心类型 | `dom_teacher.center_type=1→CBT`，`5→TBT`，其他所有值包括 `NULL→HBT` | 修正 direct/queued 映射与 fixture |
| M14 | 缺席原因字段 | `缺席原因明细` 唯一来源是 `dom_teacher_absent_reason.reason_type` | 删除 `reason_desc` 和 `appoint.cancel_reason` 的补值路径 |
| M15 | 未通知缺席 | `no_notice_cnt` 仅在同课程同教师最新 `reason_type='No Notification'` 时计数 | 不再用 `Unfilled Lesson Memo` 文案判断目标计数 |
| M16 | 假早退 | 业务整体取消假早退：目标字段、来源 payload、任务/提醒规则和 API 证据都移除；旧订阅事件只记无 payload 的退役路由位点 | 需迁移物理列并清理业务消费路由 |
| M17 | CPU/网络 | 取消 `qa_ac_classroom_record` 作为 CPU/网络来源；新来源未接入前保持 `NULL` | 不得把旧 QA 事件继续投影为确定事实 |
| M18 | 收藏/拉黑关系 | 建立、更新和计算当前关系不要求师生已有完课 | 需独立师生关系当前态，不能绑在最近课程行 |
| M19 | 收藏观察时点 | `appoint.end_time` 已确认为权威完课时间；在 `end_time+24h` 回看当时的师生收藏关系 | 需关系时间线和定时判定 |
| M20 | 收藏课程归因 | 同教师、同学生的持续收藏只归因一节课；候选先按最早 `end_time+24h`，并列时 NUMERIC ID 用 PostgreSQL 任意精度、TEXT ID 用 UTF-8 bytes，且 NUMERIC 排在 TEXT 前 | 需唯一归因约束和类型化决胜，不能每课重复加分或用 varchar 排错 9/10 |
| M21 | 延迟收藏事件 | 按业务生效时间回溯；如果改变第24小时真实状态，必须补加或扣回5分并重选唯一课程 | 需可逆结算和幂等重算，不能按 DTS 到达时间 |
| M22 | 取消后再收藏 | 师生关系当前态和时间线继续更新，但同一教师+学生终身只允许一节课获得收藏分，不开启新获分周期 | 唯一键保持为教师+学生，防止取消/重收藏重复加分 |
| M23 | TESOL | 当前证书集合中存在 `certification_code='16' AND certification_status=1` 时才有 TESOL | 需持久化/重建教师当前证书集合，不能用单个事件直接覆盖 |
| M24 | 投诉过滤 | 其他有效条件不变；`complaint_type_grandson IS NULL OR complaint_type_grandson != 82` 均可进入 | 需用 NULL 专项测试锁定语义 |
| M25 | 教师在线状态 | `status='on'` 且入职0–29天为 `NEW`，满30天为 `EXISTING`，`off→LEFT`，`hei→BLOCKED` | 需 `teachers.online_status`；第30天需日更/定时重算，不能只等 DTS |
| M26 | 状态轴拆分 | 在线状态、在营/出营状态、金牌状态是三个独立维度 | 离职/拉黑不能自动撤销出营或金牌 |
| M27 | 出营时间 | 30天内达到出营就出营；30天未达到仍保持 `IN_CAMP`，以后达到仍可出营 | 不能把第30天当作失败终态 |
| M28 | 资格时间 | 首次出营和首次金牌分别写入现有 `graduation_qualified_at` / `gold_qualified_at`，两者不可回退 | 不在 `teachers` 重复新建同义时间字段 |
| M29 | 出营分与实际分 | 出营时冻结 `graduation_score_locked=100`；`raw_total_score` 在出营和金牌后都继续累加，不封顶 | 出营快照与当前实际分必须是两个不同事实 |
| M30 | 教师端显示分 | 首次 `raw_total_score>=200` 获得金牌；教师端 `public_total_score=min(raw_total_score,200)` | 数据库真实分不能被 UI 显示上限截断 |
| M31 | 离职/拉黑后计分 | `LEFT/BLOCKED` 不停止新积分 | 在线状态不得成为结算过滤条件 |
| M32 | 人工用例 | 最终扩展为 T00–T80 及 A/B/C 后缀用例，锁定 30 天后事实保留、参与历史、缺席任务、收藏冲正、scope fence、Worker 恢复和数据库权限 | 代码完成不能只验证单个 INSERT 快乐路径 |
| M33 | 30 天边界 | 课程事实跨全日期保留；30 天只用于 `NEW→EXISTING` 和被明确命名为“新师 30 天观察”的指标 | 不得作为通用课程、评价、收藏或积分过滤器 |
| M34 | 缺席任务映射 | `Unfilled Lesson Memo`→`P-REL-MEMO`；其他非空 `reason_type`→`P-REL-ATTENDANCE`；空值不创建缺席任务 | `No Notification` 属于“其他非空”，同时单独贡献 `no_notice_cnt` |
| M35 | 在营状态 | 取消 `NOT_IN_CAMP`，只保留 `IN_CAMP / GRADUATED` | 满30天、离职、拉黑或暂未达线都不产生第三个在营状态 |
| M36 | 唯一开发契约 | 新增 `DTS_direct开发冻结实施规格.md`，目标实现冲突时覆盖旧 v1 文档 | 开发不再从人工手册的“现行行为”反推目标 |
| M37 | direct/queued 合流 | 两种模式共用事件版本、当前态、脏键和领域重算函数 | 禁止 direct 绕过持久事实直接改聚合 |
| M38 | 来源版本历史 | 新增 append-only `dts_source_row_versions` 保存受保护 before/after | A→B→A、首次 end 瞬态和关系历史不会被最新镜像吞掉 |
| M39 | 地区化脏键 | `source_region` 纳入 dirty-key 身份 | DOM/OVS 同 ID 不互相覆盖 |
| M40 | 参与序号 | A→B→A 固定 seq=1/2/3；A→NULL→B 不建 NULL 教师参与 | 每个产生参与的 appoint 事件有唯一来源版本键 |
| M41 | 完课纠错 | end 后教师/状态/时间变化或 DELETE 进入唯一 Case；只允许 KEEP/UPDATE/TRANSFER/VOID 决定 | 普通 DTS 不转移或改写冻结快照，纠错有审计和幂等冲正 |
| M42 | 子表集合选择器 | 缺席取最新、处罚 OR、DOM 评价最新、标签集合、证书 EXISTS、投诉集合、摄像头 EXISTS | 删除最新/删一留一均能从剩余记录重算 |
| M43 | 预约聚合 | `total_booked_cnt/peak_booked_cnt` 按教师参与；所有 status 计普通参与 | 系统源课程数独立去重，A/B 各有一次参与 |
| M44 | NULL/perfect | 集合不完整为 NULL；late/early 任一未知时 perfect=NULL 且不加分 | 不再把“没看到异常”当明确正常 |
| M45 | 排课 | 按当前有效 `status=on` 来源行集合，off/DELETE 回减；slot 按行 ID、days 按日期去重 | 仅作为入职 0–29 天观察指标 |
| M46 | 缺席任务纠错 | 旧 match 抑制；assignment 不删、不取消、不回退；证据课程参与级、任务教师级幂等 | 原因改型和删除不破坏终态任务 |
| M47 | OVS 评价边界 | 本轮只持久化 OVS 来源，不套 DOM 分类、不生成新 OVS 评价积分 | 兼容路径独立开关，禁止双写 |
| M48 | 集合完整性 | 新增 `dts_source_scope_states`；只有 COMPLETE 的空集合可推导 false/0 | TESOL/处罚/QA 不再误判 |
| M49 | 删除语义 | appoint/teacher 使用 tombstone 和历史保留；完课 DELETE 走纠错 | 教师 DELETE 不级联课程、积分和资格 |
| M50 | 教师端课程粒度 | 一条教师参与一行；课程积分仍一节源课程一行并只归冻结参与 | 缺席教师能看到课程但不获得该课积分 |
| M51 | 切换策略 | 新表→权威快照/基线版本→回填→DOM/OVS 对账→切 Worker/API→停旧写 | 不清表，不依赖 DTS 七天窗口，不双向写 |
| M52 | 类型化子事实 | 新增课程 current、参与 current 与标签明细；评价/投诉/QA 不直接覆盖当前教师行 | 来源镜像、领域事实和教师聚合各有唯一写入者 |
| M53 | 收藏观察任务 | 单列 `course_favorite_observations`，区分确认 false、历史不足、重试和死信 | 未知历史不能被当作未收藏，Worker 崩溃可重领 |
| M54 | scope 证明 | scope 区分 CURRENT/HISTORY，保存 snapshot ID/as-of、分区 offset 向量、行数/hash 和历史覆盖 | 空集合、false/0 与 24 小时历史真值都有可审计依据 |
| M55 | 重复差评任务 | 按冻结完课教师和 `label_id` 跨至少两节复合课程计阈值，label_name 只展示 | match 课程级、assignment 教师+标签级；纠错只抑制 match，不回退任务 |
| M56 | Worker/Outbox 所有权 | Domain Projector 只写规范化事实和 v2 Outbox；v2 Worker 唯一写聚合、任务、积分和当前资格 | 防止 direct/queued/Worker 多处重复结算 |
| M57 | 安全和恢复验收 | 新表必须有 ACL、DOM HMAC、append-only/冻结 Trigger，并验证租约崩溃重领 | 文档可直接转成数据库约束和失败路径测试 |
| M58 | 任务命中生命周期 | 四类 P 任务均冻结 match/assignment 精确键；同键恢复重新激活原 match 并增加 revision，assignment 永不删除、取消或回退 | 防止重命中重复任务或无限新增证据行 |
| M59 | scope 状态闭环 | scope 增加 GLOBAL/TEACHER 优先级、SOURCE_SCOPE 唤醒和 STALE/FAILED 恢复路径；恢复必须用新 snapshot/fence 经 LOADING/VERIFYING | false/0、历史收藏和冲正有完整性证据 |
| M60 | Outbox 身份与恢复 | `domain_aggregate_revisions` 为所有 aggregate 生成语义 revision；Outbox 只用 PENDING/PUBLISHED/DEAD_LETTER 和事务行锁，不另建 lease | 聚合事件幂等身份和崩溃恢复不再由 handler 自选 |
| M61 | 完课纠错收口 | 首次 end 无教师进入可空教师 Case；同师快照修正用 UPDATE；连续换师产生 PENDING_CORRECTION，KEEP/UPDATE/TRANSFER/VOID 按决定位置收口全部既有 PENDING | 不留下悬空参与，也不强迫“同师修时间”伪装成换师 |
| M62 | 收藏修订与再奖励 | 观察以 revision 留历史；明确 false、TRANSFER、VOID 等真实失效才冲正，之后恢复用新 generation；仅 HISTORY STALE 时已有奖励转 `AWARDED_PENDING_EVIDENCE`，保留原 +5/generation/流水，证据恢复同课则原地回 `AWARDED` | 证据暂缺不误扣，真实失效后的再奖励也不会被旧幂等键吞掉 |
| M63 | `user_complaint` 边界 | 仅保存 source version/current 供接入审计，不进入投诉 current，不产生业务脏键、v2 Outbox、指标、任务或积分 | 不用非权威表补投诉结论 |
| M64 | shadow/cutover | 新增独立 reconciliation run/cursor/result；shadow 不碰生产 Outbox，cutover/rollback 均在维护窗口按共同 fence 全量事务完成 | 影子“算过”不能冒充生产物化，失败不留半切换 |
| M65 | 旧任务迁移 | P-REL、重复差评、一般投诉、拉黑四类任务都按稳定 ID 精确迁移；歧义进入 PENDING_DATA，多行冲突停线 | 保留 assignment ID、状态、进度和终态 |
| M66 | source-only 与基线版本 | BASELINE/CDC 都进入 append-only 版本历史；退役 QA 只记无 payload 路由，`user_complaint` 为 source-only | 区分权威业务来源、审计来源和退役来源 |
| M67 | 完课决定并发与收口 | 补齐 UPDATE_COMPLETION_SNAPSHOT、连续 PENDING_CORRECTION 收口、Case revision/fingerprint/`expected_source_revision` 乐观锁；source_position 只审计 | 同师修正不伪装换师，过期运营决定无任何部分写入 |
| M68 | 类型化投诉集合 | 每条源投诉独立落 `source_course_complaints`；全部有效集合用于 L0/指标，只有最新有效行用于展示和输出 | 较旧 P0 不会被最新 P3 覆盖而漏掉资格红线 |
| M69 | 投诉与摄像头输出身份 | 冻结四类投诉路由、completion participation 级 match/Case/提醒键，以及摄像头提醒键和 TRANSFER 生命周期 | 旧教师已处理历史保留，新教师按新键物化，未处理输出可收口 |
| M70 | 非收藏逐课组件流水 | 好评、完美、Peak、硬件四组件新增 settlement、generation、奖励和唯一冲正键 | true/false/unknown、scope 失效和完课纠错均可逆且不重复记分 |
| M71 | 收藏时间证据 | 观察/关系增加时间证据状态和 WAITING_EVIDENCE；favorite 缺 add_time 时 source_timestamp 只排序、不证明历史真值 | 未知业务时间不冒充第 24 小时已收藏，也不误加 5 分 |
| M72 | 收藏修订完整性 | TRANSFER/UPDATE/VOID 失效旧观察；缺完课时间或学员不建新观察；恢复使用新 revision/generation | 延迟历史和纠错可以补扣分且旧幂等键不吞新奖励 |
| M73 | scope 恢复与优先级 | FAILED/STALE 必须以新 snapshot/fence 经 LOADING/VERIFYING 恢复；TEACHER scope 始终优先 GLOBAL | 不能直接把旧完整性证据改回 COMPLETE 或挑选更有利 scope |
| M74 | 纠错到 Worker 的唤醒 | 完课决定事务原子写 COURSE/PARTICIPATION v2 Outbox；Correction Service 同步结算，Worker 幂等刷新 match/output | 人工决定不越权写输出，也不依赖下一条 DTS 偶然触发 |
| M75 | shadow/cutover 全结果迁移 | shadow 覆盖组件、收藏观察/归因、Case/提醒计划；cutover 保留旧 ID、状态和历史，冲突停线 | 影子结果不能冒充生产写入，切换失败可全量回滚 |
| M76 | 差评标签名称缺失 | 达阈值但 log 当前 label_name 为空时只建 PENDING_DATA match，不读字典、不先建任务 | 避免选错照片执行变体；补名后复用稳定证据和 assignment |
| M77 | assignment 首次证据不可变 | DOM/OVS 后续地区、课程或规则命中只改 match/audit，任务 why/title/evidence 保留首次物化快照 | 全局教师任务不因后来证据重写历史依据 |
| M78 | 新 offset 语义重放 | 区分 NOOP、SEMANTIC_REPLAY、SOURCE_CONFLICT；只有紧邻已应用相同 transition 才吞语义重复 | A→B→A→B 的第二次 B 仍是新参与，异常 before 不被强行套用 |
| M79 | 投诉字典跨区依赖 | DOM/OVS 共用 `dom_complaint_cate`，分类键固定 dom；按三级 ID 精确 join 和 NFKC/trim/空白折叠匹配规则，并反向唤醒两区课程 | OVS 投诉先到、DOM 字典后到或改删时都能恢复，不产生 ovs 假字典键 |
| M80 | 处罚参与定位 | 同教师冻结 completion 优先，否则按 lesson_start_time 落唯一 `[assigned_at,ended_at)`；歧义保持 PENDING_DATA | A→B→A 不会把一条处罚复制给 seq1/seq3 或随机影响完美分 |
| M81 | 三项历史首次日期 | first booked/completed/open 从版本历史取最小并与 v1 旧值 LEAST 合并，旧早值标 LEGACY_FROZEN，完整双空才 CONFIRMED_EMPTY | cutover 不丢早期行为日期，普通删除/纠错不把首次日期移晚 |
| M82 | 供给里程碑 | canonical 键固定；首次确认达到 40 只加一次 10 分且永不冲正；唯一 legacy 流水用不可变 alias 映射 | 当前槽位下降不丢分，迁移不改 append-only 流水、不重复发分 |
| M83 | 投诉最新选择器 | 输出按 add_time→course_date→canonical id→`source_row_revision` 降序选择；source_position 只审计，完整 typed 集合仍用于指标/L0 | 到达顺序不改变任务、Case、提醒路由 |
| M84 | 标签 typed 排序字段 | `source_course_labels` 持久 create_time/dt/source_row_revision/source_position，按时间→log ID→revision 选同 label 当前名称 | Worker 不回读受限 source mirror，不拿 updated_at/position 代替业务修订顺序 |
| M85 | rollback 全生命周期 | 所有 assignment/已处理输出/不可逆资格与里程碑保留；v2-only 可逆课程奖冲正，retained 行再切换按原 ID/新 generation 复用 | 回滚不丢任务进度、不留孤儿输出、不造成二次结分 |
| M86 | cutover TOCTOU | 所有写方共用 shared mutex；最终 shadow 必须在 exclusive lock 内重跑并校验完整 revision vector | 相同 source fence 不再掩盖纠错、任务进度、观察或规则变化 |
| M87 | scope 快照 epoch | 新快照先 staging，再与 active epoch 做 SNAPSHOT_DIFF；不变无语义事件，GLOBAL 缺行 tombstone、TEACHER 缺行只移 membership | 恢复快照不重复建参与，也不残留已不存在的 active 集合 |
| M88 | 跨文档身份与指标 | 收藏统一 student_token/三类等待，perfect 只认冻结参与三值处罚，投诉拆两个正式字段，纠错统一 v2 Outbox | 清除数据规则与冻结规格之间最后的旧键、旧状态和 v1 链路 |
| M89 | 选择器与首快照状态机 | absent/grading/label 正式末位统一 `source_row_revision`，source_position 只审计；首次快照也只由 SNAPSHOT_DIFF 建参与，BASELINE 仅作证据 | 相同业务时间修订、重放和首次快照不再产生两套 current/participation |
| M90 | scope active/candidate 双指针 | 新增独立 epoch 表，旧 active 与装载 candidate 分离；失败半快照留痕，新 ID 重试，原子切 active | 非空/空旧集合在刷新失败时都可回退且不会提前发布 candidate |
| M91 | penalty NULL 集合语义 | 所有映射记录进入证据集合；appeal=2 不生效、NULL 为 unknown、true 优先、完整空集才 false | 避免过滤 NULL 后误判完美课和积分 |
| M92 | assignment canonical seed | Task Planner 锁 assignment key 并按稳定元组选首次证据；冻结 seed/hash；负面标签空名或执行变体冲突失败关闭 | 并发、批处理、重放和 cutover 不再随机选择 why/title/照片流程 |
| M93 | scope 写权与 cutover 决定收口 | Scope Coordinator 独占 epoch/staging/membership 和 BASELINE/SNAPSHOT_DIFF 发布；cutover 同事务按 projection_event_ids 重算决定状态 | ACL 有唯一 writer，纠错决定不会在绕过 Worker 后永久停在 PENDING |
| M94 | Broker epoch 与初始 H0 | 路由内全部 partition 的 epoch identity、opening/floor offset、checkpoint 和控制审计由 `bootstrap_initial_broker_epoch_v2` 按完整 H0 vector 一次事务建立；禁止逐 partition 提交 | 初始启动、topic reset 和响应丢失重试不会形成半个代际 |
| M95 | scope 激活清单 | 每次激活必须精确覆盖配置路由的全部 CURRENT scope；appoint、schedule、favorite、blacklist 另要求 HISTORY；空路由只用 `__route_empty__/ROUTE_EMPTY/GLOBAL/*` sentinel | 少表、少地区、少层级或假空集都不能发布 COMPLETE |
| M96 | 快照 candidate 与 diff 顺序 | table-level projection generation、candidate lease；每 snapshot+table 只对实际 diff key 按 typed numeric→UTF-8 text 做 1-based ordinal，同一 key 的 step1 为奇数、仅 bootstrap 后正常差异的 step2 为偶数；operation不与奇偶绑定 | 首快照、B→A、缺行恢复和 Worker 崩溃均有确定身份与顺序 |
| M97 | 接入失败事实 | envelope/epoch 校验失败写 `dts_ingest_issues`，按 delivery identity+payload HMAC+key version 幂等；只有同一原 delivery 全部合法落账/推进 checkpoint 的事务才能 RESOLVED/ACK | 非法 delivery 不被静默 ACK，也不保存 raw payload/密钥 |
| M98 | 收藏 held 读写闭环 | `course_favorite_attributions` 显式支持 `AWARDED_PENDING_EVIDENCE`；读取侧仍计一条 +5 并返回 evidence status，恢复同候选不新建流水 | 总分、课程明细和证据状态保持一致 |
| M99 | 模板与英文 copy 发布 | 模板/copy 只能经受限发布函数换版；RETIRED 终态。单模板只 fan-out 同 task_code 的未物化计划，copy 发布 fan-out 全部未物化计划；缺/冲突 copy 不 fallback | 换版不会改旧 assignment，也不会漏重算待创建任务 |
| M100 | copy 物理引用 | `config_versions` 只允许四个精确 key，保存 schema_version/payload_hash；assignment 冻结 `(teacher_copy_version_id,teacher_copy_config_key)` 复合 FK | 禁止跨配置域引用或运行时回读常量形成第二真相源 |
| M101 | TASK_PLAN cutover 分支 | final run 固定 `EXISTING_EVENT/CUTOVER_PLANNED/ALREADY_APPLIED` 三分支；无 aggregate 的 base=0，planned=1；异 payload、revision 漂移和晚到 base event 均失败关闭或只留 supersede 审计 | 响应丢失、并发与首次物化不产生第二份任务 |
| M102 | 读路由与时间追平 | `dts_projection_read_routes` 单行原子切 v1/v2；cutover 主事务后仍保持维护态，耐久时间事件追平 COMPLETE 后才开放服务 | 切读点和定时观察/日更边界不会出现半新半旧 |
| M103 | 物化与积分 origin | match、组件 settlement 和 `score_entries` 分别冻结 materialization/entry origin、run 与 projection generation；rollback 只冲正 v2 可逆课程奖励，不伪装删除事实 | rollback/re-cutover 能证明每条输出来自哪一代且不重复结分 |
| M104 | 固定任务积分 owner | 三种 pipeline mode 都只有 Fixed Task Score Settler 写 `FIXED_TASK_AWARD`；SourceWide 不补造，双方经共享账户锁重建积分/资格 | 课程全量与任务完成并发时仍只有一条 canonical 流水 |
| M105 | legacy Outbox 归档 | 只有两类 typed proof 可经受限函数原子 archive+audit+delete；row/proof/payload safety hash 均由数据库重算，同 run/hash 重放精确 no-op | 不能为迁移放宽生产三态，也不能归档敏感或未知旧工作 |
| M106 | compatibility 窗口任务 | expand 后、cutover 前 v1 首次新建个性化任务标 `LEGACY_COMPAT`，冻结当时 template/copy/英文文案/evidence/timezone/due；与 expand 前 `LEGACY_FROZEN` 分开 | 窗口任务不丢失，也不被 cutover 重新解释成 MATCH |
| M107 | source revision 最终顺序 | current selector、expected revision 和状态推进最后一位只认 `source_row_revision`；`source_position` 仅同 epoch 诊断，不构造跨 partition 全序 | 业务结果不再依赖 connector 到达位置 |
| M108 | 投诉规则原子发布 | 原始规则行、文件 SHA、typed 规则、规则版本和发布审计统一由 `complaint_rule_imports` 与受限发布函数一次事务完成 | 删除旧 `source_records` 双事实源，发布失败不留半套规则 |
| M109 | dirty 工作队列闭环 | 地区化脏键改用 key-local work revision、输入账本、唯一领取 owner、租约和受控恢复 | 不跨来源误比 revision，崩溃/依赖晚到也不吞重算 |
| M110 | 恢复与并发防重 | Outbox、收藏观察和 dirty 恢复均校验 expected generation/revision/version；处理中新增证据会重新排队 | 响应丢失或旧恢复命令不会误恢复下一代工作，也不覆盖新证据 |
| M111 | TASK_PLAN blocker 与已有任务 | plan state 使用真实 copy version number并纳入 blocker；已有 assignment 恢复时复用原 generation/basis | 缺 copy、负面标签待补和任务已物化后的恢复不会产生第二个任务或重选文案 |
| M112 | shadow 结果注册表 | 对账固定 14 类结果，补 `SCORE_COMPONENT_ACCOUNT`；积分流水只允许 REUSE/ALIAS/AWARD/REVERSE | cutover 不漏子项账户，也不重复或改写 append-only 积分流水 |
| M113 | 完课纠错 mode 门禁 | Correction Service 只在 V2_PRIMARY 接受决定；DUAL_CAPTURE/ROLLED_BACK 只读 Case、提交返回维护错误 | 避免 v1/v2 两个 owner 同写课程积分，或决定写入后无人投影 |
| M114 | 投诉规则全局代次 | active rule set 使用固定 catalog identity 与全局单调 activation_generation，SHA/content hash作为版本证据 | 连续换版不会因每个文件自己的 revision 相同而漏唤醒课程 |
| M115 | 教师端契约单一真相源 | teacher 目录只保留根共享任务契约入口，不再复制整份字段/状态机 | 后续开发只维护一份任务契约，避免镜像漂移 |
| M116 | 参与反向约束修正 | 只有 current/COMPLETION 角色存在时课程才必须反向指回；仅有历史参与时指针可空 | A→NULL、VOID和历史缺席参与可以合法保留，不强迫伪造当前教师 |
| M117 | snapshot diff 稳定身份 | 实际 diff key 按 typed 比较生成1-based ordinal；同key step1/step2固定奇偶位点 | 快照重放不会因数据库排序或0/1起点不同产生另一套版本身份 |
| M118 | cutover 输出生命周期 | shadow 的 Case/提醒计划补自动取消、恢复原行和保留已处理/已读动作 | 来源失效/恢复不会留下错误状态，也不会重建第二份输出 |
| M119 | copy换版先重评match | 新copy先更新未物化match的标题、variant、evidence/hash和负面标签blocker，再每key生成一次plan revision | 未来任务不会混用旧match与新copy，也不会走错照片/通用流程 |
| M120 | 供给里程碑配置锁定 | v1 固定 CAPACITY_PEAK_SLOT_40、阈值40和分值10，配置发布拒绝修改 | 不需要猜补差、换键或二次发奖规则，保持不可逆里程碑唯一 |
| M121 | 收藏规则换版结算 | 收藏AWARDED/held按可逆组件冲正旧代、generation+1重奖；REVERSED不重开 | 单价换版后账户、归因和append-only流水一致，不漏算或重复 |
| M122 | 积分规则换版并发 | 课程、固定任务、V2纠错与共用重建在规则读取至提交期间持共享锁并做锁后版本校验；发布独占并原子重算 | 旧规则事务不能晚提交覆盖新账户或资格 |
| M123 | 任务模板分值冻结 | 首次发布和换版逐编码校验G01–G09固定分值、五个P任务0分，异值整笔拒绝 | 模板换版不能绕过30分结构或让个性化任务产分 |

## 3. 本次复审已对主手册做的修正

| 编号 | 修正 | 原因 |
|---|---|---|
| R01 | 从在营状态中删除 `NOT_IN_CAMP` | 已确认只有在营和成功出营，不根据满30天、离职或拉黑推导第三状态 |
| R02 | 在 7.1 确认课程事实全日期保留 | 解除“30天课程准入”与“实际分持续累加”的冲突 |
| R03 | 修正 7.6 代课示例中 A/B 预约课次的过度断言，并在后续冻结参与聚合 | 最终确定所有未删除普通参与均计 booked，Peak 再要求 `is_peak=true`；纠错角色不计 |
| R04 | 把 12.2 的“延迟场景需确版”改为“已确版” | 已明确要按业务生效时间补/扣分 |
| R05 | 收藏归因确认为同一教师+学生终身唯一 | 关系仍持续更新，但取消后再收藏不开启新获分周期 |
| R06 | 修正验收完成条件中对乱序事件的表述 | 已确认缺席原因和收藏需要与到达顺序无关，不能再把现行丢弃当成可接受目标 |
| R07 | 在主手册顶部增加本清单链接和实现阻断说明 | 防止下一步开发只看事件正文，忽略未确版的结构性问题 |
| R08 | 新增冻结实施规格并同步主手册、数据库、课程和读取契约 | 消除“一课一行”与“一课多参与”的权威冲突 |
| R09 | 把 T08/T19/K05 从“永久 ignored”改为来源持久化后恢复 | 与乱序结果无关的 P0 决策一致 |
| R10 | 把 T53–T80 及全部后缀用例加入目标验收 | 覆盖参与回环、多行集合、纠错、删除、跨区字典、处罚定位、收藏时间证据、scope fence、Worker 恢复、影子切换和数据库权限 |
| R11 | 确定旧聚合、NULL、排课、任务纠错和 OVS 范围 | 不让 Worker/积分实现自行猜测 |
| R12 | 增加 T73–T80，并同步共享任务、数据库和积分契约 | 覆盖 scope fence、完课决定复开、VOID、收藏未知、Worker 崩溃、9 项任务基线和数据库权限 |
| R13 | 最终同步任务 lifecycle、scope 恢复、aggregate revision、收藏 generation、source-only 路由和原子切换 | 消除冻结规格、数据库、共享任务、积分规则与人工用例之间的实现级二义性 |
| R14 | 补齐跨区投诉字典、处罚参与定位、收藏时间证据和完课决定 Outbox 唤醒 | 关闭最后一轮会迫使开发者自行选择实现语义的 P0 缺口 |
| R15 | 补齐历史迁移、不可变里程碑 alias、选择器物理字段、快照 epoch、cutover mutex 与 rollback 生命周期 | 使存量切换、失败回退和再次切换也有唯一实现答案 |
| R16 | 终审把正式选择器统一到 source_row_revision，并补齐 scope 双指针/写权、处罚三值集合、任务 canonical seed 和 cutover 决定投影收口 | position 回归诊断属性，业务 current 不再依赖到达位置 |
| R17 | 补齐 whole-vector H0、精确 scope manifest、candidate/diff、ingest issue、模板/copy 发布、TASK_PLAN cutover、读路由/时间追平和 origin matrix | 数据库状态机、迁移、Worker 与失败恢复均有同一份物理协议 |
| R18 | 区分收藏证据暂缺与真实失效，并同步 held 读取；冻结 Fixed Task Score Settler 唯一 owner | 避免误扣收藏分和全量重建补造固定任务分 |
| R19 | 增加 LEGACY_COMPAT 与 legacy Outbox 类型化归档协议，删除旧投诉导入表/外部水位 owner/中文任务标题 | expand-contract 窗口与旧数据清理不再迫使实现者自行猜测 |

## 4. 当前代码与目标口径的主要差距

本节冻结的是进入开发前的代码审查基线，用于追溯变更依据；其中列出的差距已由后续 rev66–100
及运行时代码逐项收敛，不应再作为代码 head 的现状描述。数据库执行、真实 DTS 消费和生产验收仍需
在部署阶段独立证明。

| 代码区域 | 现行行为 | 目标差距 |
|---|---|---|
| `backend/app/db_models.py` 的 `lesson_source_wide` | `课程id` 是单一主键，一课只有一个 `老师id` | 无法表达“一个源课程 + A/B 多个教师参与 + 完课归属冻结” |
| `backend/app/db_models.py` 的 `lesson_score_results` | `lesson_id` 一课一份结算 | 引入教师参与后，计分身份键和课程事实键需重新定义 |
| `backend/app/dts_direct_projector.py` 课程准入 | 仍过滤 `appoint.use_point!='buy'` 和 `status in ('cancel','on')` | 与 M06/M07 冲突 |
| `backend/app/dts_direct_projector.py` 缺席 | appoint 会读 `cancel_reason`，absence 会优先读 `reason_desc` | 与 M04/M14/M15 冲突 |
| `backend/app/dts_direct_projector.py` 评价 | 仍是旧 `score/type` OR 逻辑 | 未按 `grading.use_point` 分支 |
| `backend/app/dts_direct_projector.py` 评价标签 | 只追加名称，且仍有 `type/status` 过滤 | 缺少 `label_id+label_name` 明细事实和删除/更新重算 |
| `backend/app/dts_direct_projector.py` TESOL | 根据单条事件的旧类型/状态直接覆盖 | 未按教师当前证书集合判断 code 16 |
| `backend/app/dts_direct_projector.py` 收藏 | 绑定到已有宽表中的某节课程 | 缺独立关系当前态、时间线、+24h 评估、唯一归因和冲正 |
| `backend/app/dts_direct_projector.py` QA | 仍路由假早退和 `qa_ac_classroom_record` | 与 M16/M17 冲突 |
| `backend/app/dts_source_consumer.py` 字段白名单 | appoint 仍允许 `cancel_reason`，absence 仍允许 `reason_desc`；grading 缺 `use_point`，certification 缺 `certification_code` | 消费层尚不足以向投影层提供新口径字段 |
| `backend/app/dts_wide_projector.py` | queued 路径仍保留旧课程、缺席、TESOL、标签、QA 和关系逻辑 | 如果 queued 模式仍是受支持路径，必须与 direct 共用同一份领域判定而非各自修一遍 |
| `backend/app/db_models.py` 的 `teachers` | 暂无 `online_status`、`graduation_score_locked` | 未完整表达 M25/M29；资格时间则已在 `teacher_qualifications` 存在，无需重复建列 |
| `backend/app/source_wide_worker.py` 与共享任务结算 | 已有实际分、对外显示分和不可逆资格的部分基础 | 应复用而不是另建一套分数；需补出营分快照、在线状态和新的课程/收藏事实 |

以上表格描述实现前差距；当前实现分支不修改本地 `release`，最终发布仍须按项目发布授权边界执行。

## 5. 本轮已确版的 P0 决策

| 项 | 已确版决策 | 实现约束 |
|---|---|---|
| P0-1 | 课程事实全日期保留；30 天只限制 `NEW→EXISTING` 和被明确指定的新师观察指标 | 课程、评价、收藏、投诉和可计分事实不得因超过 30 天被丢弃 |
| P0-2 | 新建“源课程+教师参与”两层事实 | 源课程唯一键为 `(source_region, source_appoint_id)`；参与行唯一键为 `(source_region, source_appoint_id, participation_seq)`；不拼接假课程 ID |
| P0-3 | 源课程数和教师参与次数分开 | A→B 时源课程数是 1、参与数是 2；`total_booked_cnt/peak_booked_cnt` 固定按教师普通参与/Peak 参与统计，所有 status 形成普通参与 |
| P0-4 | 同一教师+学生终身只允许一节课获收藏分 | 关系时间线仍完整保留；取消后重新收藏不新建获分周期 |
| P0-5 | `Unfilled Lesson Memo`→`P-REL-MEMO`；其他非空 `reason_type`→`P-REL-ATTENDANCE`；空值不创建缺席任务 | `No Notification` 除触发出席任务外，另单独增加 `no_notice_cnt`；任务幂等不等于通知已送达 |
| P0-6 | 先持久化事件版本、当前态和时间线，再幂等重建领域事实 | A→B→A 中间转换、缺席原因早到、收藏延迟和冲正事件不得在未持久化时 ignored 并 ACK |
| P0-7 | 取消 `NOT_IN_CAMP`，在营状态仅为 `IN_CAMP / GRADUATED` | 满30天未出营仍为 `IN_CAMP`；`LEFT/BLOCKED` 仅属于在线状态，不停止积分，不改变在营状态 |

完课归属继续按已确版规则处理：课程首次进入 `end` 时，冻结当时教师参与行为完课归属；之后 `t_id` 变化不静默转移课程积分。课程子事实先关联源课程，有明确 `t_id` 时再关联对应参与行。

## 6. 原 P1 问题的冻结结果

1. 地区：`course` 含 `global_cn/global_pool`→ovs，其他非空→dom，无法判断为 NULL；变化触发重算，不删除课程，冲突时停止新结算。
2. 入职日：唯一使用 `date(status_on_time)`；北京业务日期用于 NEW/EXISTING 和日更。`is_full_time/job_days` 不参与本轮 DTS 课程或积分判断，不阻断本轮实现。
3. Peak：本轮沿用主手册已列 DOM/OVS 本地时段；无法证明本地时间/地区时 `is_peak=NULL`。节假日和额外 DST 推断不在本轮范围，不得自行扩展。
4. 排课：采用当前有效槽集合；off/DELETE 回减，slot 按来源行 ID，days 按日期去重，只统计入职 0–29 天。
5. OVS 评价：本轮明确排除新分类和积分，只持久化；DOM 规则不得套用。
6. absent：只由参与状态 `t_absent` 计；原因不额外计 absent，处罚只形成 late/early。
7. perfect：late/early 都有完整证据且明确 false 才为 true；任一未知为 NULL/SOURCE_MISSING。
8. favorite/block 当前人数按关系学员去重；两个旧 rate 固定 NULL。其余比率公式见冻结实施规格 7.3。
9. TESOL：true/false/NULL 三值，空集合只有 scope COMPLETE 时为 false。
10. CPU/网络 API 保留字段并显式返回 null；新来源接入前不得省略、不得写 false、不得计分。

## 7. 建议的代码实现批次

不建议把所有问题塞进一个迁移和一次投影修改。合理顺序是：

1. **契约与模型批次**：按已确版 P0-1/P0-2/P0-6/P0-7 修订数据库契约，通过 Alembic 增加源课程、教师参与、完课冻结、关系时间线和标签明细。
2. **消费契约批次**：修正白名单、`dom/dmo`、退役表路由和事件幂等键；保证字段先可被消费。
3. **课程/代课垂直切片**：实现一课多教师、A→B、乱序缺席原因、首次 `end` 冻结，并按冻结规格验证事实和已确版指标。
4. **评价/标签/TESOL 批次**：用持久明细和当前集合重算派生字段。
5. **关系时间线批次**：先实现收藏/拉黑当前态，再实现 +24h 定时评估、唯一归因和延迟冲正。
6. **教师状态/资格批次**：增加在线状态和第30天定时转换，复用现有资格时间和 raw/public 分数机制，补出营分快照。
7. **聚合与计分切换批次**：按已确版 P0-3/P0-4/P0-5 修正聚合、计分、缺席任务意图和教师端读取；未有回执的提醒/外部动作不得宣称已送达。
8. **存量重建与对账批次**：独立迁移 `dmo→dom`、重建受影响宽表，分 DOM/OVS 对账事件账本、checkpoint、源当前态和下游分数。

## 8. 开发和验收停线条件

下列任一情况出现时，不能把该批次写成完成：

1. 为了让测试通过，在代码中自行选择了某个 P0 业务口径。
2. 只修 direct 但 queued/重算路径仍能写回旧口径，或反之。
3. 用一节伪造课程代替一条教师参与记录，但未修订下游计分和唯一键契约。
4. 乱序事件仍在无持久化情况下 ignored 并 ACK，却声称结果与到达顺序无关。
5. 用课程宽表的某一行代替师生关系当前态或关系时间线。
6. 把任务行已创建写成已通知/已执行，或在没有真实消费回执时声称外部动作完成。
7. 只验证本地单测，没有 Alembic 检查、数据库约束、重放幂等、checkpoint/回滚和存量对账证据。

代码实现完成后，仍需回到主手册的 T00–T80 及全部后缀用例逐项留证，再判断是否可启用 SourceWide Worker 和不可逆资格门禁。
