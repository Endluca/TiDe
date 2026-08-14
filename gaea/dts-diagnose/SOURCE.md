# Aliyun DTS official diagnostic package

- Official troubleshooting guide: <https://help.aliyun.com/zh/dts/user-guide/troubleshoot-issues-in-change-tracking-tasks>
- Source repository: <https://github.com/silly-fofo/dts-subscribe-demo>
- Pinned source commit: `48596de62f01ea7d4b84082b562c33e9e5350287`
- Bundled file: `dts_subscribe_sdk_dep_demo-1.0-SNAPSHOT-jar-with-dependencies.jar`
- Size: `13,917,673` bytes
- SHA-256: `8a1c484a7c01fc5e684b57eb720a4757f3652027c6d1fea41bc5f69451556ef0`
- Main class: `com.aliyun.dts.subscribe.clients.DTSConsumerDemo`
- Build JDK: Java 8
- Embedded Kafka client: `1.0.0`

The file is the JAR linked from the Aliyun troubleshooting guide. It is pinned
because the upstream repository is not an internal artifact registry and the
JAR is not signed. The image build performs no external download.

This is a full diagnostic consumer, not a read-only Metadata probe. In
`ASSIGN` mode it can consume records and update the selected DTS consumer
group's position. It also logs decoded record content. Use it only for the
bounded PRE diagnosis documented in `gaea/README.md`, then switch the project
back to the `dts-ingest` module.

The bundle includes legacy Kafka 1.0.0, Log4j 1.2.17, Fastjson 1.2.31 and DTS
SDK 1.4.0 dependencies. They are retained only to reproduce the vendor's
official diagnostic path and are not an approved production runtime.
