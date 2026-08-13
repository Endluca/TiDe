#!/usr/bin/env bash
set -euo pipefail

combined_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
contract_probe="${combined_dir}/contract-probe.sql"

fail() {
  printf '联合部署预检失败：%s\n' "$1" >&2
  exit 1
}

parse_ipv4() {
  local address="$1"
  local label="$2"
  local octet_1 octet_2 octet_3 octet_4 octet

  [[ "${address}" =~ ^[^:]+$ ]] \
    || fail "${label} 暂不接受 IPv6；必须使用显式 IPv4"
  [[ "${address}" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] \
    || fail "${label} 必须是显式 IPv4"
  IFS='.' read -r octet_1 octet_2 octet_3 octet_4 <<<"${address}"
  for octet in "${octet_1}" "${octet_2}" "${octet_3}" "${octet_4}"; do
    [[ "${octet}" =~ ^(0|[1-9][0-9]{0,2})$ ]] \
      || fail "${label} 必须使用无前导零的规范 IPv4"
    ((10#${octet} <= 255)) || fail "${label} 含非法 IPv4 段"
  done
  PARSED_IPV4_INT=$(( \
    (10#${octet_1} << 24) \
    | (10#${octet_2} << 16) \
    | (10#${octet_3} << 8) \
    | 10#${octet_4} \
  ))
}

is_rfc1918_ipv4() {
  local address_int="$1"
  ((
    (address_int >= 167772160 && address_int <= 184549375)
    || (address_int >= 2886729728 && address_int <= 2887778303)
    || (address_int >= 3232235520 && address_int <= 3232301055)
  ))
}

is_loopback_or_rfc1918_ipv4() {
  local address_int="$1"
  ((
    (address_int >= 2130706432 && address_int <= 2147483647)
  )) || is_rfc1918_ipv4 "${address_int}"
}

parse_cidr() {
  local cidr="$1"
  local label="$2"
  local minimum_prefix="$3"
  local maximum_prefix="$4"
  local address prefix

  [[ "${cidr}" == */* && "${cidr#*/}" != */* ]] \
    || fail "${label} 必须是单个显式 IPv4 CIDR"
  address="${cidr%/*}"
  prefix="${cidr##*/}"
  [[ "${prefix}" =~ ^(0|[1-9][0-9]?)$ ]] \
    || fail "${label} 前缀格式不安全"
  ((10#${prefix} >= minimum_prefix && 10#${prefix} <= maximum_prefix)) \
    || fail "${label} 前缀必须位于 /${minimum_prefix} 到 /${maximum_prefix}"

  parse_ipv4 "${address}" "${label}"
  PARSED_CIDR_ADDRESS_INT="${PARSED_IPV4_INT}"
  PARSED_CIDR_PREFIX=$((10#${prefix}))
  PARSED_CIDR_MASK=$(( \
    (0xFFFFFFFF << (32 - PARSED_CIDR_PREFIX)) \
    & 0xFFFFFFFF \
  ))
  PARSED_CIDR_NETWORK_INT=$(( \
    PARSED_CIDR_ADDRESS_INT & PARSED_CIDR_MASK \
  ))
  PARSED_CIDR_BROADCAST_INT=$(( \
    PARSED_CIDR_NETWORK_INT | (0xFFFFFFFF ^ PARSED_CIDR_MASK) \
  ))
  ((PARSED_CIDR_ADDRESS_INT == PARSED_CIDR_NETWORK_INT)) \
    || fail "${label} 必须填写规范网络地址，不能携带主机位"
}

required_variables=(
  TIDE_OPS_HOST
  TIDE_TEACHER_HOST
  TIDE_EDGE_BIND_ADDRESS
  TIDE_EDGE_NETWORK_SUBNET
  TIDE_EDGE_PROXY_IP
  TIDE_COMPANY_GATEWAY_CIDR
  TIDE_DATABASE_NAME
  TIDE_OPS_ENV_FILE
  TIDE_OPS_MIGRATION_ENV_FILE
  TIDE_TEACHER_ENV_FILE
  TIDE_TEACHER_REPO_PATH
  TIDE_TEACHER_EXPECTED_COMMIT
  TIDE_TEACHER_PUBLIC_ASSET_BASE_URL
)

for variable_name in "${required_variables[@]}"; do
  [[ -n "${!variable_name:-}" ]] || fail "缺少 ${variable_name}"
done

[[ "${TIDE_OPS_HOST}" != "${TIDE_TEACHER_HOST}" ]] \
  || fail "运营端与教师端必须使用不同域名"
[[ "${TIDE_OPS_HOST}" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] \
  || fail "运营端域名格式不安全"
[[ "${TIDE_TEACHER_HOST}" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] \
  || fail "教师端域名格式不安全"
[[ "${TIDE_DATABASE_NAME}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] \
  || fail "目标数据库名格式不安全"
[[ "${TIDE_TEACHER_PUBLIC_ASSET_BASE_URL}" == https://* ]] \
  || fail "教师端公共素材地址必须是 HTTPS"
parse_ipv4 "${TIDE_EDGE_BIND_ADDRESS}" "Edge 监听地址"
edge_bind_int="${PARSED_IPV4_INT}"
is_loopback_or_rfc1918_ipv4 "${edge_bind_int}" \
  || fail "Edge 监听地址只能使用 loopback 或 RFC1918 私网 IPv4"

parse_ipv4 "${TIDE_EDGE_PROXY_IP}" "Edge 容器可信代理地址"
edge_proxy_int="${PARSED_IPV4_INT}"
parse_cidr "${TIDE_EDGE_NETWORK_SUBNET}" "Edge 容器网络" 8 30
edge_network_int="${PARSED_CIDR_NETWORK_INT}"
edge_network_broadcast_int="${PARSED_CIDR_BROADCAST_INT}"
edge_network_mask="${PARSED_CIDR_MASK}"
is_rfc1918_ipv4 "${edge_network_int}" \
  && is_rfc1918_ipv4 "${edge_network_broadcast_int}" \
  || fail "Edge 容器网络必须完整位于同一个 RFC1918 私网段"
(( (edge_proxy_int & edge_network_mask) == edge_network_int )) \
  || fail "Edge 容器可信代理地址不在声明的容器网络内"
(( edge_proxy_int != edge_network_int \
    && edge_proxy_int != edge_network_broadcast_int )) \
  || fail "Edge 容器可信代理地址不能是网络地址或广播地址"

# 信任范围必须足够具体；更宽的私网段会让同网段直连者伪造客户端地址。
parse_cidr "${TIDE_COMPANY_GATEWAY_CIDR}" "公司网关可信源" 24 32
company_gateway_network_int="${PARSED_CIDR_NETWORK_INT}"
company_gateway_broadcast_int="${PARSED_CIDR_BROADCAST_INT}"
is_loopback_or_rfc1918_ipv4 "${company_gateway_network_int}" \
  && is_loopback_or_rfc1918_ipv4 "${company_gateway_broadcast_int}" \
  || fail "公司网关可信源必须完整位于 loopback 或 RFC1918 私网"

[[ "${TIDE_OPS_ENV_FILE}" == /* ]] || fail "运营端环境文件必须使用绝对路径"
[[ "${TIDE_OPS_MIGRATION_ENV_FILE}" == /* ]] \
  || fail "运营端迁移环境文件必须使用绝对路径"
[[ "${TIDE_TEACHER_ENV_FILE}" == /* ]] || fail "教师端环境文件必须使用绝对路径"
[[ "${TIDE_OPS_ENV_FILE}" != "${TIDE_OPS_MIGRATION_ENV_FILE}" ]] \
  || fail "运营运行与迁移环境文件不得复用"
[[ "${TIDE_OPS_ENV_FILE}" != "${TIDE_TEACHER_ENV_FILE}" ]] \
  || fail "TiDe 后端与教师后端运行账号不得复用"
[[ "${TIDE_TEACHER_REPO_PATH}" == /* ]] || fail "教师端仓库必须使用绝对路径"
[[ "${TIDE_TEACHER_EXPECTED_COMMIT}" =~ ^[0-9a-f]{40}$ ]] \
  || fail "教师端固定提交必须是完整 40 位 SHA"
[[ -f "${TIDE_OPS_ENV_FILE}" ]] || fail "运营端生产环境文件不存在"
[[ -f "${TIDE_OPS_MIGRATION_ENV_FILE}" ]] || fail "运营端迁移环境文件不存在"
[[ -f "${TIDE_TEACHER_ENV_FILE}" ]] || fail "教师端生产环境文件不存在"
protected_environment_files=(
  "${TIDE_OPS_ENV_FILE}"
  "${TIDE_OPS_MIGRATION_ENV_FILE}"
  "${TIDE_TEACHER_ENV_FILE}"
)
for ((left_index = 0; left_index < ${#protected_environment_files[@]}; left_index++)); do
  for ((right_index = left_index + 1; right_index < ${#protected_environment_files[@]}; right_index++)); do
    [[ ! "${protected_environment_files[left_index]}" \
          -ef "${protected_environment_files[right_index]}" ]] \
      || fail "运行与迁移环境文件不得通过软链接或硬链接复用"
  done
done
python3 - \
  "${TIDE_OPS_ENV_FILE}" \
  "${TIDE_OPS_MIGRATION_ENV_FILE}" \
  "${TIDE_TEACHER_ENV_FILE}" \
  "${TIDE_DATABASE_NAME}" <<'PY' \
  || fail "数据库账号与最终角色契约不一致"
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

runtime_path = Path(sys.argv[1])
migration_path = Path(sys.argv[2])
teacher_path = Path(sys.argv[3])
expected_database = sys.argv[4]


def read_values(path: Path) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        candidate = value.strip()
        if (
            len(candidate) >= 2
            and candidate[0] == candidate[-1]
            and candidate[0] in {"'", '"'}
        ):
            candidate = candidate[1:-1]
        values.setdefault(key.strip(), []).append(candidate)
    return values


def validate_url(raw_url: str, expected_role: str) -> None:
    parsed = urlparse(
        raw_url.replace("postgresql+psycopg://", "postgresql://", 1)
    )
    if (
        parsed.scheme != "postgresql"
        or unquote(parsed.username or "") != expected_role
        or unquote(parsed.path.removeprefix("/")) != expected_database
        or parse_qs(parsed.query, keep_blank_values=True).get("sslmode")
        != ["verify-full"]
    ):
        raise SystemExit(
            f"database URL must use {expected_role}, target "
            f"{expected_database}, and sslmode=verify-full"
        )


runtime_values = read_values(runtime_path)
runtime_urls = runtime_values.get("DATABASE_URL", [])
expected_values = runtime_values.get("TIT_SOURCE_WORKER_EXPECTED_DATABASE", [])
if (
    len(runtime_urls) != 1
    or not runtime_urls[0]
    or expected_values != [expected_database]
):
    raise SystemExit("missing or ambiguous TiDe runtime database settings")
validate_url(runtime_urls[0], "tit_growth_app")

migration_urls = read_values(migration_path).get("DATABASE_URL", [])
if len(migration_urls) != 1 or not migration_urls[0]:
    raise SystemExit("missing or ambiguous migration DATABASE_URL")
validate_url(migration_urls[0], "tide_sys_admin")

teacher_values = read_values(teacher_path)
for variable_name in ("TIDE_DATABASE_URL", "SHIWEN_READ_DATABASE_URL"):
    teacher_urls = teacher_values.get(variable_name, [])
    if len(teacher_urls) != 1 or not teacher_urls[0]:
        raise SystemExit(f"missing or ambiguous {variable_name}")
    validate_url(teacher_urls[0], "tit_teacher_crud")
PY
git -C "${TIDE_TEACHER_REPO_PATH}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || fail "教师端路径不在 Git 工作副本内"

actual_commit="$(
  git -C "${TIDE_TEACHER_REPO_PATH}" rev-parse HEAD
)"
[[ "${actual_commit}" == "${TIDE_TEACHER_EXPECTED_COMMIT}" ]] \
  || fail "教师端提交不是已评审固定版本：${actual_commit}"

source_git_root="$(
  git -C "${TIDE_TEACHER_REPO_PATH}" rev-parse --show-toplevel
)"
[[ -z "$(git -C "${source_git_root}" status --porcelain)" ]] \
  || fail "部署源码所在 Git 工作副本存在未提交改动"

teacher_service="${TIDE_TEACHER_REPO_PATH}/backend/src/tide/tide.service.ts"
teacher_catalog="${TIDE_TEACHER_REPO_PATH}/backend/scripts/sync-current-task-catalog.ts"
teacher_growth="${TIDE_TEACHER_REPO_PATH}/backend/src/notifications/growth-stage-notification.repository.ts"
teacher_migrator="${TIDE_TEACHER_REPO_PATH}/backend/database/scripts/apply-production.sh"
teacher_semantic_migration="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0025_fixed_task_semantic_alignment.up.sql"
teacher_legacy_view_retirement="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0028_retire_task_business_change_view.up.sql"
teacher_unused_cleanup="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0029_remove_unused_tide_objects.up.sql"
teacher_unused_columns_cleanup="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0030_remove_unused_columns_and_orphan_function.up.sql"
teacher_g04_migration="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0031_g04_independent_sections.up.sql"
teacher_onboarding_migration="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0032_first_login_onboarding.up.sql"
teacher_g01_tesol_only_migration="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0033_g01_tesol_only.up.sql"
teacher_g04_two_part_migration="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0037_g04_remove_device_check.up.sql"
teacher_personalized_photo_migration="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0038_personalized_environment_photo.up.sql"
teacher_g02_document_migration="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0039_g02_policy_document.up.sql"
teacher_g02_read_status_migration="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0040_g02_document_read_status.up.sql"
teacher_crm_sso_migration="${TIDE_TEACHER_REPO_PATH}/backend/database/migrations/0041_crm_sso_hybrid.up.sql"

[[ -f "${teacher_service}" ]] || fail "缺少教师端任务服务"
[[ -f "${teacher_catalog}" ]] || fail "缺少教师端任务目录同步器"
[[ -f "${teacher_growth}" ]] || fail "缺少教师端成长阶段读取逻辑"
[[ -f "${teacher_semantic_migration}" ]] \
  || fail "缺少教师端 0025 固定任务语义迁移"
[[ -f "${teacher_legacy_view_retirement}" ]] \
  || fail "缺少教师端 0028 旧业务变化视图退役迁移"
[[ -f "${teacher_unused_cleanup}" ]] \
  || fail "缺少教师端 0029 无用对象清理迁移"
[[ -f "${teacher_unused_columns_cleanup}" ]] \
  || fail "缺少教师端 0030 冗余列与孤儿函数清理迁移"
[[ -f "${teacher_g04_migration}" ]] \
  || fail "缺少教师端 0031 G04 三模块迁移"
[[ -f "${teacher_onboarding_migration}" ]] \
  || fail "缺少教师端 0032 首次登录引导迁移"
[[ -f "${teacher_g01_tesol_only_migration}" ]] \
  || fail "缺少教师端 0033 G01 TESOL-only 迁移"
[[ -f "${teacher_g04_two_part_migration}" ]] \
  || fail "缺少教师端 0037 G04 两模块迁移"
[[ -f "${teacher_personalized_photo_migration}" ]] \
  || fail "缺少教师端 0038 个性化环境拍照迁移"
[[ -f "${teacher_g02_document_migration}" ]] \
  || fail "缺少教师端 0039 G02 原生文档迁移"
[[ -f "${teacher_g02_read_status_migration}" ]] \
  || fail "缺少教师端 0040 G02 阅读状态迁移"
[[ -f "${teacher_crm_sso_migration}" ]] \
  || fail "缺少教师端 0041 CRM SSO 混合认证迁移"
[[ -f "${contract_probe}" ]] \
  || fail "缺少联合部署数据库契约探针"
grep -Fq "20260813_60_dom_privacy" "${contract_probe}" \
  || fail "数据库契约探针未固定最终 public head 20260813_60_dom_privacy"
grep -q "2026-08-05-g04-three-part" "${teacher_g04_migration}" \
  || fail "教师端 0031 未发布经评审的 G04 三模块版本"
grep -q "g02-device-2026-08-05-browser-preflight-v1" "${teacher_g04_migration}" \
  || fail "教师端 0031 未包含经评审的设备基础预检"
grep -q "g02-courseware-2026-08-05-guidance-v1" "${teacher_g04_migration}" \
  || fail "教师端 0031 未包含经评审的备课须知确认版本"
grep -q "2026-08-05-g04-three-part-v1" "${teacher_g04_migration}" \
  || fail "教师端 0031 未包含经评审的三模块完成规则版本"
grep -q "tide.account_onboarding_states" "${teacher_onboarding_migration}" \
  || fail "教师端 0032 未创建账号引导状态事实表"
grep -q "security_event.event_type = 'LOGIN'" "${teacher_onboarding_migration}" \
  || fail "教师端 0032 未按成功登录事件识别存量账号"
grep -q "security_event.outcome = 'SUCCESS'" "${teacher_onboarding_migration}" \
  || fail "教师端 0032 未限定成功登录结果"
grep -q "'MIGRATED_EXISTING'" "${teacher_onboarding_migration}" \
  || fail "教师端 0032 未标记存量已登录账号"
grep -q "2026-08-11-tesol-only-v1" "${teacher_g01_tesol_only_migration}" \
  || fail "教师端 0033 未固定 G01 TESOL-only 规则版本"
grep -q "teacher_failure_copy = 'TESOL 真实状态尚未通过。'" "${teacher_g01_tesol_only_migration}" \
  || fail "教师端 0033 未发布经评审的 TESOL-only 失败文案"
grep -q "2026-08-11-g04-two-part" "${teacher_g04_two_part_migration}" \
  || fail "教师端 0037 未发布 G04 两模块版本"
grep -q "DELETE FROM tide.task_step_definitions" "${teacher_g04_two_part_migration}" \
  || fail "教师端 0037 未移除当前 G04 设备步骤定义"
grep -q '"requiredStepKeys":\["g02-environment-photo","g02-courseware-confirmation"\]' \
  "${teacher_g04_two_part_migration}" \
  || fail "教师端 0037 完成规则不是照片与课件准备两项"
grep -q "p-fb-negative-environment-photo" \
  "${teacher_personalized_photo_migration}" \
  || fail "教师端 0038 未包含个性化环境拍照步骤"
grep -q "TEACHING_ENVIRONMENT_V1" \
  "${teacher_personalized_photo_migration}" \
  || fail "教师端 0038 未锁定授课环境 AI 审核档案"
grep -q '"contentStatus":"PENDING"' \
  "${teacher_personalized_photo_migration}" \
  || fail "教师端 0038 未保持 P-FB-NEGATIVE 默认失败关闭"
grep -q "step_key = 'g02-policy-document'" \
  "${teacher_g02_document_migration}" \
  || fail "教师端 0039 未固定 G02 原生文档步骤"
grep -q "step_type = 'DOCUMENT'" \
  "${teacher_g02_document_migration}" \
  || fail "教师端 0039 未把 G02 步骤固定为 DOCUMENT"
grep -q '"contentHash"' "${teacher_g02_document_migration}" \
  || fail "教师端 0039 未固定 G02 文档内容哈希"
grep -q "task_step_progress_g02_assignment_completion_check" \
  "${teacher_g02_read_status_migration}" \
  || fail "教师端 0040 未建立 G02 阅读完成跨表约束"
grep -q "CREATE TABLE tide.crm_sso_logins" "${teacher_crm_sso_migration}" \
  || fail "教师端 0041 未创建 CRM SSO 一次性登录事实"
grep -q "ALTER COLUMN password_hash DROP NOT NULL" "${teacher_crm_sso_migration}" \
  || fail "教师端 0041 未允许 SSO 账号无本地密码"
grep -q "auth_method" "${teacher_crm_sso_migration}" \
  || fail "教师端 0041 未记录会话认证方式"

if grep -Eq "HIDDEN_FIXED_TASK_CODES.*G02|new Set\\(\\['G02'\\]\\)" "${teacher_service}"; then
  fail "教师端仍隐藏当前 G02 平台政策任务"
fi
if grep -q "code: 'G10'" "${teacher_catalog}"; then
  fail "教师端执行目录仍使用退役编码 G10"
fi
if grep -q "'G10'" "${teacher_growth}"; then
  fail "教师端成长阶段逻辑仍使用退役编码 G10"
fi

python3 - "${teacher_catalog}" <<'PY' \
  || fail "教师端执行目录不是精确的新 G01-G09 标题与分值"
from __future__ import annotations

import re
import sys
from pathlib import Path

catalog_path = Path(sys.argv[1])
catalog_source = catalog_path.read_text(encoding="utf-8")
expected = {
    "G01": ("Profile & Credentials Completion", 3),
    "G02": ("Platform Policies", 2),
    "G03": ("How to handle different types of students", 2),
    "G04": ("Lesson Preparation", 3),
    "G05": ("TTP Orientation", 3),
    "G06": ("ME Culture & PARSNIP", 4),
    "G07": ("Reliability Training", 3),
    "G08": ("Cocos Course Training", 5),
    "G09": ("SET Teaching Fundamentals", 5),
}
task_start = re.compile(
    r"^\s+code:\s*(?P<code_quote>['\"])(?P<code>G\d{2})(?P=code_quote),\s*$"
    r"\n^\s+title:\s*(?P<title_quote>['\"])(?P<title>.*?)(?P=title_quote),\s*$",
    re.MULTILINE,
)
matches = list(task_start.finditer(catalog_source))
declared_codes = re.findall(
    r"^\s+code:\s*['\"](G\d{2})['\"],\s*$",
    catalog_source,
    re.MULTILINE,
)
actual: dict[str, tuple[str, int]] = {}
duplicate_codes: set[str] = set()
for index, match in enumerate(matches):
    code = match.group("code")
    block_end = matches[index + 1].start() if index + 1 < len(matches) else len(
        catalog_source
    )
    block = catalog_source[match.end() : block_end]
    score_match = re.search(r"^\s+score:\s*(\d+),\s*$", block, re.MULTILINE)
    if score_match is None:
        raise SystemExit(f"{code} has no static integer score")
    if code in actual:
        duplicate_codes.add(code)
    actual[code] = (match.group("title"), int(score_match.group(1)))

if sorted(declared_codes) != sorted(expected) or duplicate_codes or actual != expected:
    raise SystemExit(
        "teacher catalog mismatch: "
        f"declared={declared_codes!r}, "
        f"duplicates={sorted(duplicate_codes)}, actual={actual!r}"
    )
PY

[[ -f "${teacher_migrator}" ]] || fail "缺少教师端正式生产迁移器"
grep -Fq "public Alembic 46 -> teacher 0028 -> public head 50 -> teacher 0032 -> public head 54 -> teacher 0037 -> public head 55 -> public head 56 -> teacher 0038 -> public head 57 -> teacher 0040 -> teacher 0041" \
  "${teacher_migrator}" \
  || fail "教师端迁移器缺少 public46→teacher0028→public50→teacher0032→public54→teacher0037→public55→release-public56→teacher0038→release-public57→teacher0040→teacher0041 分阶段失败关闭门禁"
grep -Fq "20260811_56_p_fb_negative_copy" "${teacher_migrator}" \
  || fail "教师端迁移器未固定 release public 56 切换点"
grep -Fq "20260811_57_g02_document" "${teacher_migrator}" \
  || fail "教师端迁移器未固定 release public 57 切换点"
grep -Fq "product_analytics_recorded" "${teacher_migrator}" \
  || fail "教师端迁移器未区分历史 0020 是否已经记录"
[[ -f "${TIDE_TEACHER_REPO_PATH}/backend/Dockerfile" ]] \
  || fail "缺少教师端 API 生产镜像"
[[ -f "${TIDE_TEACHER_REPO_PATH}/frontend/Dockerfile" ]] \
  || fail "缺少教师端 Web 生产镜像"
python3 - "${teacher_migrator}" <<'PY' \
  || fail "教师端生产迁移器不是以 0041 结尾的 36 条完整有序生产链"
from __future__ import annotations

import re
import sys
from pathlib import Path

migrator_source = Path(sys.argv[1]).read_text(encoding="utf-8")
expected = [
    "0001_initial",
    "0002_shared_database_exchange",
    "0003_file_upload_intents",
    "0004_task_command_receipts",
    "0005_faq_message_commands",
    "0006_teacher_profile_g01_support",
    "0007_shared_task_assignment_links",
    "0008_remove_legacy_task_exchange",
    "0009_task_view_command",
    "0010_current_task_execution",
    "0011_system_notification_delivery",
    "0012_system_notification_publication_guards",
    "0013_system_notification_owner_maintenance",
    "0014_teacher_photo_processing",
    "0015_teacher_photo_filter_strength",
    "0016_database_quiz_banks",
    "0019_growth_stage_notification_state",
    "0020_product_analytics",
    "0021_teacher_support_tickets",
    "0022_performance_job_leases",
    "0023_teacher_support_operator_atomicity",
    "0024_support_ticket_cas_and_function_owner",
    "0025_fixed_task_semantic_alignment",
    "0026_kuozhi_course_syncs",
    "0027_remove_local_quiz_runtime",
    "0028_retire_task_business_change_view",
    "0029_remove_unused_tide_objects",
    "0030_remove_unused_columns_and_orphan_function",
    "0031_g04_independent_sections",
    "0032_first_login_onboarding",
    "0033_g01_tesol_only",
    "0037_g04_remove_device_check",
    "0038_personalized_environment_photo",
    "0039_g02_policy_document",
    "0040_g02_document_read_status",
    "0041_crm_sso_hybrid",
]
target_match = re.search(
    r'TARGET_MIGRATION="\$\{TIDE_MIGRATION_TARGET:-([^}]+)\}"',
    migrator_source,
)
list_match = re.search(
    r"^PRODUCTION_MIGRATIONS=\(\s*$"
    r"(?P<body>.*?)"
    r"^\)\s*$",
    migrator_source,
    re.MULTILINE | re.DOTALL,
)
if target_match is None or list_match is None:
    raise SystemExit("cannot parse production migration manifest")
actual = re.findall(
    r"^\s+([0-9]{4}_[a-z0-9_]+)\s*$",
    list_match.group("body"),
    re.MULTILINE,
)
if target_match.group(1) != expected[-1] or actual != expected:
    raise SystemExit(
        "teacher migration manifest mismatch: "
        f"target={target_match.group(1)!r}, actual={actual!r}"
    )
PY
[[ -f "${TIDE_TEACHER_REPO_PATH}/backend/Dockerfile.migrate" ]] \
  || fail "缺少教师端独立生产迁移镜像"
if grep -Eq "0017_task_assignment_teacher_response|0018_remove_task_assignment_teacher_response" "${teacher_migrator}"; then
  fail "教师端生产迁移器仍越权修改 public.task_assignments"
fi

printf '联合部署静态预检通过；数据库必须按 public46→teacher0028→public50→teacher0032→public54→teacher0037→public55→release-public56→teacher0038→release-public57→teacher0040→teacher0041→public59→public60 执行，最终必须通过 public 20260813_60_dom_privacy / teacher 0041 契约探针和发布门禁。\n'
