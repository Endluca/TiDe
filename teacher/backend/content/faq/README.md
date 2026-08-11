# Canonical FAQ 内容源

`51Talk Teacher FAQ - Canonical.md` 是教师端 FAQ 的唯一版本化内容源。

- 内容修改必须经过业务审核，并同步递增文档 `source_version`。
- 发布前运行 `pnpm import:faq -- --dry-run` 校验数量、格式、版本和内容哈希。
- 运行服务只读取数据库中已激活的 `knowledge_documents`、`knowledge_chunks` 和 FAQ Prompt 版本，不直接读取 Markdown。
- 禁止直接修改数据库中的 FAQ 正文；所有更新都从本文件经过受控导入发布。
