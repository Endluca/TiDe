# DTS source profile 元数据观察工具

状态：离线可运行，尚未接入生产 DTS transport。

## 边界

`backend/scripts/observe_dts_source_metadata.py` 只读取阿里云官方 Java SDK 已解码后的
结构化 `EVENT` JSONL，聚合以下元数据：

- 表名和操作；
- before/after image 是否存在及字段名；
- 从 Avro image 外层结构观察到的抽象类型；
- SDK `Field.dataTypeNumber`；
- 相同签名的 `sample_count`。

工具不会把业务值、topic、partition、offset、record id、事务位置、账号、消费组写入结果，
不会导入 PostgreSQL store，不写 checkpoint，也不会发送 DTS/Kafka ACK。输入 JSONL 本身可能
含业务值，必须放在受限临时目录，不得提交 Git、上传普通配置区或复制到日志。

现有 `OfficialJavaDtsTransport.run()` 以“数据库事务完成后发送 `DURABLE_ACK_BATCH`”为硬契约，
不能安全复用为无 ACK 观察器。因此当前版本没有 broker/live 模式，也不能据此宣称已经连接生产
DTS。以后若增加 live source，必须使用单独授权的 diagnostic group，并继续禁止 PostgreSQL、
正式 group checkpoint 和双 ACK。

## 配置与运行

复制
`backend/.env.dts-source-metadata.production.example` 到 Git 工作区外，由受控运行环境注入。
运行时必须显式设置：

```text
TIT_DTS_SOURCE_METADATA_OBSERVER_ENABLED=true
TIT_DTS_SOURCE_METADATA_SOURCE_MODE=offline_jsonl
TIT_DTS_SOURCE_METADATA_REGION=dom 或 ovs
TIT_DTS_SOURCE_METADATA_DIAGNOSTIC_GROUP_ID=<专用诊断系统 group ID>
TIT_DTS_SOURCE_METADATA_FORMAL_GROUP_ID=<该地区正式系统 group ID，仅用于判定不相等>
TIT_DTS_SOURCE_METADATA_INPUT_JSONL=<受限目录中的输入文件>
TIT_DTS_SOURCE_METADATA_OUTPUT=<输出 JSON 文件>
TIT_DTS_SOURCE_METADATA_MAX_EVENTS=<正整数，最大 1000000>
TIT_DTS_SOURCE_METADATA_MAX_DURATION_SECONDS=<正数，最大 86400>
```

diagnostic group 与 formal group 相同会失败关闭。当前离线实现不会实际使用两者连接 broker，
该校验用于防止后续把正式 group 误带入观察流程。如果运行环境已经存在正式 consumer 的
`TIT_DTS_GROUP_ID`，它还必须与这里声明的 formal group 完全一致，否则同样失败关闭。

在 `backend/` 执行：

```bash
.venv/bin/python scripts/observe_dts_source_metadata.py
```

也可以用 `--region / --input-jsonl / --output / --max-events /
--max-duration-seconds` 覆盖对应的非敏感运行参数；显式启用和两项 group 门禁只能由环境变量提供。

输入每行必须是 official_java 协议中的一个完整、非 lightweight `EVENT` 对象。控制事件可以存在，
但不进入证据；没有任何 DML 观察结果时命令失败。输出先写同目录 0600 临时文件，完成 `fsync`
后用 `os.replace` 原子替换目标文件。stdout 只打印与文件相同的 canonical JSON；失败时 stderr
只打印稳定 `error_code`。

## 结果审核

`evidence_sha256` 是删除该字段后，对其余 canonical JSON（包括 `source_region`）计算的
SHA-256。观察结果只是物理字段证据候选，不是业务 profile，也不会自动修改
`backend/app/dts_source_profiles_v2.json`。

人工批准至少要确认：

1. 地区和物理表身份正确；
2. INSERT/UPDATE/DELETE 的 before/after 字段集合及稀疏模式已覆盖；
3. 同一字段的抽象类型和 `dataTypeNumber` 没有漂移；
4. 样本数量和观察窗口足以支持判断；
5. 国内敏感字段的保护边界另行审核。

完成审核后，才能由独立变更把批准的字段、image mode、类型证据、来源说明和该 SHA 写入
`dts_source_profiles_v2.json`，再走代码评审、测试和发布门禁。观察工具本身不得生成或发布正式
manifest。
