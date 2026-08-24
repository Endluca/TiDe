# DTS source scope snapshot 协调器

状态：可运行的受限操作入口；不代表已经取得生产源快照。

## 边界

入口为 `backend/scripts/run_dts_source_scope_snapshot.py`。它只读取外部系统已经导出的
JSON/JSONL 证据文件，并调用 PostgreSQL rev100 的受保护 v3 函数：

```text
begin → stage rows → verify → publish → readback
```

它不会连接源库、DTS 或 Kafka，不会查询学生原始身份，也不会自行补齐来源数据。
`dry-run` 只校验文件、专用数据库角色、Schema/ACL 和目标 head/scope，不写数据库。

当前 rev100 的发布边界：

- 只接受 `scope_kind=CURRENT`；HISTORY 会失败关闭；
- candidate 绑定审核后的 profile ID 与完整字段类型，发布时锁定 broker checkpoint，合并
  `[start_next_offset,end_next_offset)` 内已提交 CDC，生成确定性的 `SNAPSHOT_DIFF`；
- 新 key 写 `SNAPSHOT_INSERT`，变化写 `SNAPSHOT_UPDATE`；GLOBAL 缺 key 写 tombstone，
  TEACHER 缺 key 只移除该 scope membership，不删除全局 source current；
- 发布后的 CDC 在同一事务更新 active GLOBAL/TEACHER membership overlay。未被 checkpoint
  覆盖、epoch 血缘不一致或 profile/哈希不一致时仍返回稳定错误并失败关闭。

空 candidate 只有在权威源导出明确为空、fence 已到达且 profile/哈希全部一致时才表示完整空集；
GLOBAL 空集会删除该表当前集合，TEACHER 空集只清该教师 membership。程序不会把
`SOURCE_MISSING`、文件缺行或“查不到数据”猜成完整空集。

## 运行身份

数据库连接只从 `TIT_DTS_SCOPE_COORDINATOR_DATABASE_URL` 读取，不回退到通用
`DATABASE_URL`。登录用户和当前用户都必须精确为
`tit_dts_scope_coordinator_runtime`，且该角色必须保持：

```text
LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
```

角色只能读取 scope 状态并执行 rev100 的 SECURITY DEFINER 函数，不能直接 INSERT/UPDATE/DELETE
scope 表。数据库 URL、token 和候选行不会进入命令输出。

非敏感配置模板见
[`backend/dts-scope-coordinator.production.env.example`](../backend/dts-scope-coordinator.production.env.example)。
数据库 URL 必须通过部署平台敏感变量单独注入。

## 输入证据

生产优先使用 JSONL，便于大文件流式校验和分批 stage。文件顺序固定为：

1. 一条 `record_type=snapshot_header`；
2. 零到多条按类型化 `id` 严格升序的 `record_type=snapshot_row`；
3. 一条 `record_type=snapshot_manifest`。

Header 的必要字段：

```json
{
  "record_type": "snapshot_header",
  "protocol_version": "dts-source-scope-snapshot-candidate-v1",
  "snapshot_id": "scope-v1:<candidate_sha256>",
  "source_region": "dom",
  "source_table": "dom_teacher",
  "scope_kind": "CURRENT",
  "scope_level": "GLOBAL",
  "scope_key": "*",
  "snapshot_as_of": "2026-08-22T12:00:00.000000Z",
  "snapshot_consistency_token": "<源系统一致性快照 token>",
  "snapshot_consistency_token_sha256": "<sha256>",
  "source_profile": {
    "manifest_version": 1,
    "manifest_sha256": "<独立 profile 文件原始字节 sha256>",
    "profile_id": "dts-source-schema:v2:<sha256>"
  },
  "source_export_evidence_sha256": "<权威导出证据 sha256>",
  "fence_evidence_sha256": "<token 到 broker fence 适配证明 sha256>",
  "partition_offsets": [
    {
      "source_region": "dom",
      "source_partition_epoch_id": "epoch:v1:<sha256>",
      "topic": "<topic>",
      "partition_id": 0,
      "start_next_offset": 100,
      "end_next_offset": 120
    }
  ],
  "history_from": null,
  "history_through": null
}
```

每行只允许三个业务字段：

```json
{
  "record_type": "snapshot_row",
  "source_key_data": {"id": 123},
  "dependency_keys": {
    "category_ids": [],
    "course_ids": [],
    "label_ids": [],
    "student_subjects": [],
    "teacher_ids": ["123"]
  },
  "protected_source_row": {"id": 123, "status": "on"}
}
```

`protected_source_row` 字段集合必须与独立 profile 的
`persisted_protected_fields` 精确一致，且 profile 字段不得超出 v2 业务白名单。DOM 不接受
`s_id/student_id/stu_id/user_id`；需要学员
依赖时只能使用 `dom:v1:<HMAC-SHA256>` token。依赖数组必须去重并按 UTF-8 升序。

Trailer 固定为：

```json
{
  "record_type": "snapshot_manifest",
  "row_count": 1,
  "content_hash": "<行 manifest 的 canonical JSON sha256>",
  "fence_hash": "<规范化 partition_offsets 的 canonical JSON sha256>",
  "candidate_sha256": "<候选证据 canonical JSON sha256>"
}
```

`snapshot_id` 由 `candidate_sha256` 确定；begin/verify/publish command ID 也由同一哈希确定，
所以同一证据重试不会创建第二个 candidate，换证据必须使用新的 snapshot ID。文件本身的原始字节
SHA-256 和 profile 文件原始字节 SHA-256 必须通过命令参数或环境变量从独立发布记录注入，不能由本次
运行结果反向视为“已审核”。

JSON 格式使用同一字段，把 rows 放在根对象的 `rows` 数组，并把 trailer 四字段放在根对象。

## 操作顺序

```bash
cd backend

# 1. 只检查专用角色、Schema、函数 ACL；不写库
.venv/bin/python scripts/run_dts_source_scope_snapshot.py health

# 2. 离线验证 profile/candidate/隐私/排序/所有哈希；不连库
.venv/bin/python scripts/run_dts_source_scope_snapshot.py validate \
  --candidate /reviewed/snapshot.jsonl \
  --profile-manifest /reviewed/dts_source_profiles_v2.json \
  --expected-artifact-sha256 "$CANDIDATE_FILE_SHA256" \
  --expected-profile-manifest-sha256 "$PROFILE_MANIFEST_SHA256"

# 3. 读取目标 head/scope；不 begin、不 stage
.venv/bin/python scripts/run_dts_source_scope_snapshot.py dry-run \
  --candidate /reviewed/snapshot.jsonl \
  --profile-manifest /reviewed/dts_source_profiles_v2.json \
  --expected-artifact-sha256 "$CANDIDATE_FILE_SHA256" \
  --expected-profile-manifest-sha256 "$PROFILE_MANIFEST_SHA256"

# 4. 显式发布。中断后用完全相同文件、哈希和 owner 重跑
.venv/bin/python scripts/run_dts_source_scope_snapshot.py apply \
  --candidate /reviewed/snapshot.jsonl \
  --profile-manifest /reviewed/dts_source_profiles_v2.json \
  --expected-artifact-sha256 "$CANDIDATE_FILE_SHA256" \
  --expected-profile-manifest-sha256 "$PROFILE_MANIFEST_SHA256"

# 5. 独立读回；只返回状态、计数和哈希，不返回 token/行
.venv/bin/python scripts/run_dts_source_scope_snapshot.py readback \
  --snapshot-id "scope-v1:$CANDIDATE_SHA256"
```

`apply` 对 stage 分批提交并续租。批次失败可用原证据安全重跑：已 stage 的完全相同行返回 NOOP；
同键不同 payload 失败关闭。verify 失败会把 candidate 置为 FAILED，原 snapshot ID 不得复用。

## 生产执行前仍需外部提供

仓库当前的 `backend/app/dts_source_profiles_v2.json` 刻意为空，代码不能据白名单生成生产 profile。
真正执行前仍必须由源系统/DTS 负责人提供并审核：

- 对应物理表的完整 selected-column、主键类型和 image mode profile；
- 同一源事务的一致性 snapshot token 与完整 scope 导出；
- 版本化适配器给出的 token → broker epoch/next-offset fence 证明；
- 已保护、无原始学生 PII、按类型化主键排序的候选行；
- profile 文件、候选文件、导出证据和 fence 证据的独立 SHA-256 发布记录；
- 目标库 broker epoch、checkpoint 与 candidate fence 的现场读回，以及发布后
  source current、membership、dirty key、generation manifest 的独立对账。

缺少其中任一项，只能执行 `health`；不能把本地测试通过写成生产 scope 已 COMPLETE。
