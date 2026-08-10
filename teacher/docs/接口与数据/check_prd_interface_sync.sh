#!/usr/bin/env bash

set -u

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
sync_root="$(cd -- "$script_dir/../.." && pwd)"

if ! git -C "$sync_root" rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "未找到 Git 仓库，无法检查文档联动。"
  exit 1
fi

prd_rel="docs/PRD/新师训练营_教师端系统_PRD.md"
contract_rel="docs/接口与数据/教师端共享任务表契约.md"
schema_rel="docs/接口与数据/数据库表结构.md"
own_schema_rel="docs/接口与数据/教师端自有表结构.md"
score_entry_rel="docs/接口与数据/教师端积分与课程读取对照表.md"

for rel in "$prd_rel" "$contract_rel" "$schema_rel" "$own_schema_rel" "$score_entry_rel"; do
  if [ ! -f "$sync_root/$rel" ]; then
    echo "缺少当前文档：$rel"
    exit 1
  fi
done

if ! grep -Fq '状态：唯一当前契约' "$sync_root/$contract_rel"; then
  echo "共享任务表契约未标记为唯一当前契约。"
  exit 1
fi

if ! grep -Fq '状态：权威入口' "$sync_root/$schema_rel" ||
   ! grep -Fq '../../../docs/数据库表结构.md' "$sync_root/$schema_rel"; then
  echo "数据库表结构入口未指向根目录唯一当前文档。"
  exit 1
fi

if ! grep -Fq '状态：权威入口' "$sync_root/$score_entry_rel" ||
   ! grep -Fq '../../../contracts/教师端积分与课程读取对照表.md' "$sync_root/$score_entry_rel"; then
  echo "积分与课程读取入口未指向根目录唯一当前契约。"
  exit 1
fi

if ! grep -Fq '../接口与数据/教师端共享任务表契约.md' "$sync_root/$prd_rel" ||
   ! grep -Fq '../接口与数据/数据库表结构.md' "$sync_root/$prd_rel"; then
  echo "PRD 未同时引用当前共享任务契约和数据库表结构。"
  exit 1
fi

sync_version='TIDE-SHARED-DB-20260730-14'
for rel in "$prd_rel" "$contract_rel" "$own_schema_rel"; do
  if ! grep -Fq "$sync_version" "$sync_root/$rel"; then
    echo "文档联动口径版本不一致：$rel"
    exit 1
  fi
done

echo "PRD 与当前数据契约检查通过：$sync_version"
