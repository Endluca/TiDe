# MR #60 DMS 迁移包

适用且只适用以下起点：

- public：`20260819_63_dts_direct_privacy`
- teacher：`0041_crm_sso_hybrid`，账本 36 条

执行文件：

`20260819_mr60_public63_teacher0041_to_public65_teacher0042.sql`

SHA-256：

`b5599ed6fd9909d8cf1a971674e74cafc7fc2dd93eea857d17c80eeb6982a439`

执行要求：

1. 仅在对应应用提交已冻结、备份和维护窗口已确认后执行。
2. 在 DMS 中一次性执行完整文件，不要拆段，也不要单独修改迁移账本。
3. 脚本以单一事务执行；版本、文案、任务身份、Trigger 或 Tide 执行配置有任何未知漂移时会整体回滚。
4. 成功后保留脚本末尾三组读回结果：public head、teacher head，以及 G05/G08/G09 的中文运营名和英文五项完整文案。

该脚本不会回退任务状态、积分或既有完成事实。已有 G08 assignment 只把退役的 Cocos 展示文案原位修正为 Global Communicator，并递增 `row_version`。
