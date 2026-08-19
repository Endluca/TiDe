#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
TEACHER_DATABASE_ROOT = ROOT / "teacher" / "backend" / "database"
TEACHER_MIGRATOR = TEACHER_DATABASE_ROOT / "scripts" / "apply-production.sh"
TEACHER_MIGRATIONS = TEACHER_DATABASE_ROOT / "migrations"

APPROVED_TEST_HOST = (
    "ai-efficiency-postgresql-20260722194941.pods.test.51talk.biz"
)
APPROVED_TEST_PORT = "5432"
APPROVED_TEST_DATABASE = "tit_growth_test_v2"
APPROVED_OWNER_ROLE = "postgres"
APPROVED_SSLMODE = "disable"

CONFIG_KEYS = frozenset(
    {
        "TIDE_ADMIN_DB_HOST",
        "TIDE_ADMIN_DB_PORT",
        "TIDE_ADMIN_DB_USER",
        "TIDE_ADMIN_DB_NAME",
        "TIDE_ADMIN_DB_PASSWORD",
        "TIDE_ADMIN_DB_SSLMODE",
    }
)
DISALLOWED_LIBPQ_ENV = frozenset(
    {
        "PGAPPNAME",
        "PGCHANNELBINDING",
        "PGCLIENTENCODING",
        "PGCONNECT_TIMEOUT",
        "PGDATABASE",
        "PGFALLBACKAPPLICATIONNAME",
        "PGGSSENCMODE",
        "PGGSSLIB",
        "PGHOST",
        "PGHOSTADDR",
        "PGKRBSRVNAME",
        "PGLOADBALANCEHOSTS",
        "PGOPTIONS",
        "PGPASSFILE",
        "PGPASSWORD",
        "PGPORT",
        "PGREQUIREPEER",
        "PGREQUIRESSL",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGSSLCERT",
        "PGSSLCRL",
        "PGSSLCRLDIR",
        "PGSSLCOMPRESSION",
        "PGSSLKEY",
        "PGSSLMODE",
        "PGSSLNEGOTIATION",
        "PGSSLROOTCERT",
        "PGTARGETSESSIONATTRS",
        "PGUSER",
    }
)

PUBLIC_50 = "20260810_50_g04_sections"
PUBLIC_51 = "20260811_51_g01_tesol_only"
PUBLIC_54 = "20260811_54_g04_remove_device_check"
PUBLIC_55 = "20260811_55_source_wide_v12"
PUBLIC_56 = "20260811_56_p_fb_negative_copy"
PUBLIC_57 = "20260811_57_g02_document"
PUBLIC_59 = "20260812_59_simple_acl"
PUBLIC_60 = "20260813_60_dom_privacy"
PUBLIC_61 = "20260814_61_teacher_copy"
PUBLIC_62 = "20260818_62_dts_claim_idx"
PUBLIC_63 = "20260819_63_dts_direct_privacy"
PUBLIC_64 = "20260819_64_g05_g08_courses"
PUBLIC_65 = "20260819_65_g09_set_course"
TEACHER_32 = "0032_first_login_onboarding"
TEACHER_33 = "0033_g01_tesol_only"
TEACHER_37 = "0037_g04_remove_device_check"
TEACHER_38 = "0038_personalized_environment_photo"
TEACHER_39 = "0039_g02_policy_document"
TEACHER_40 = "0040_g02_document_read_status"
TEACHER_41 = "0041_crm_sso_hybrid"
TEACHER_42 = "0042_g09_set_kuozhi_course"

PUBLIC_REVISION_CHAIN = (
    (
        "20260810_50_g04_independent_sections.py",
        PUBLIC_50,
        "20260807_49_unused_columns",
    ),
    ("20260811_51_g01_tesol_only.py", PUBLIC_51, PUBLIC_50),
    ("20260811_54_g04_remove_device_check.py", PUBLIC_54, PUBLIC_51),
    ("20260811_55_source_wide_contract_v12.py", PUBLIC_55, PUBLIC_54),
    ("20260811_56_p_fb_negative_copy.py", PUBLIC_56, PUBLIC_55),
    ("20260811_57_g02_policy_document.py", PUBLIC_57, PUBLIC_56),
    ("20260813_60_dom_student_privacy.py", PUBLIC_60, PUBLIC_59),
    ("20260814_61_teacher_copy.py", PUBLIC_61, PUBLIC_60),
    ("20260818_62_dts_claim_idx.py", PUBLIC_62, PUBLIC_61),
    ("20260819_63_dts_direct_privacy.py", PUBLIC_63, PUBLIC_62),
    ("20260819_64_g05_g08_courses.py", PUBLIC_64, PUBLIC_63),
    ("20260819_65_g09_set_course.py", PUBLIC_65, PUBLIC_64),
)


@dataclass(frozen=True)
class DatabaseState:
    public_head: str
    teacher_head: str


@dataclass(frozen=True)
class UpgradeAction:
    schema: str
    target: str
    expected_state: DatabaseState


FINAL_STATE = DatabaseState(PUBLIC_65, TEACHER_42)
TRANSITIONS: Mapping[DatabaseState, UpgradeAction] = {
    DatabaseState(PUBLIC_50, TEACHER_32): UpgradeAction(
        "public", PUBLIC_54, DatabaseState(PUBLIC_54, TEACHER_32)
    ),
    DatabaseState(PUBLIC_51, TEACHER_32): UpgradeAction(
        "public", PUBLIC_54, DatabaseState(PUBLIC_54, TEACHER_32)
    ),
    DatabaseState(PUBLIC_54, TEACHER_32): UpgradeAction(
        "teacher", TEACHER_37, DatabaseState(PUBLIC_54, TEACHER_37)
    ),
    DatabaseState(PUBLIC_54, TEACHER_33): UpgradeAction(
        "teacher", TEACHER_37, DatabaseState(PUBLIC_54, TEACHER_37)
    ),
    DatabaseState(PUBLIC_54, TEACHER_37): UpgradeAction(
        "public", PUBLIC_55, DatabaseState(PUBLIC_55, TEACHER_37)
    ),
    DatabaseState(PUBLIC_55, TEACHER_37): UpgradeAction(
        "public", PUBLIC_56, DatabaseState(PUBLIC_56, TEACHER_37)
    ),
    DatabaseState(PUBLIC_56, TEACHER_37): UpgradeAction(
        "teacher", TEACHER_38, DatabaseState(PUBLIC_56, TEACHER_38)
    ),
    DatabaseState(PUBLIC_56, TEACHER_38): UpgradeAction(
        "public", PUBLIC_57, DatabaseState(PUBLIC_57, TEACHER_38)
    ),
    DatabaseState(PUBLIC_57, TEACHER_38): UpgradeAction(
        "teacher", TEACHER_40, DatabaseState(PUBLIC_57, TEACHER_40)
    ),
    DatabaseState(PUBLIC_57, TEACHER_39): UpgradeAction(
        "teacher", TEACHER_40, DatabaseState(PUBLIC_57, TEACHER_40)
    ),
    DatabaseState(PUBLIC_57, TEACHER_40): UpgradeAction(
        "teacher", TEACHER_41, DatabaseState(PUBLIC_57, TEACHER_41)
    ),
    DatabaseState(PUBLIC_57, TEACHER_41): UpgradeAction(
        "public", PUBLIC_59, DatabaseState(PUBLIC_59, TEACHER_41)
    ),
    DatabaseState(PUBLIC_59, TEACHER_41): UpgradeAction(
        "public", PUBLIC_60, DatabaseState(PUBLIC_60, TEACHER_41)
    ),
    DatabaseState(PUBLIC_60, TEACHER_41): UpgradeAction(
        "public", PUBLIC_61, DatabaseState(PUBLIC_61, TEACHER_41)
    ),
    DatabaseState(PUBLIC_61, TEACHER_41): UpgradeAction(
        "public", PUBLIC_62, DatabaseState(PUBLIC_62, TEACHER_41)
    ),
    DatabaseState(PUBLIC_62, TEACHER_41): UpgradeAction(
        "public", PUBLIC_63, DatabaseState(PUBLIC_63, TEACHER_41)
    ),
    DatabaseState(PUBLIC_63, TEACHER_41): UpgradeAction(
        "public", PUBLIC_64, DatabaseState(PUBLIC_64, TEACHER_41)
    ),
    DatabaseState(PUBLIC_64, TEACHER_41): UpgradeAction(
        "public", PUBLIC_65, DatabaseState(PUBLIC_65, TEACHER_41)
    ),
    DatabaseState(PUBLIC_65, TEACHER_41): UpgradeAction(
        "teacher", TEACHER_42, FINAL_STATE
    ),
}


def _fail(message: str) -> RuntimeError:
    return RuntimeError(message)


def _reject_ambient_libpq_environment() -> None:
    present = sorted(name for name in DISALLOWED_LIBPQ_ENV if name in os.environ)
    if present:
        raise _fail(
            "安全检查失败：请先清除这些 libpq 环境变量："
            + ", ".join(present)
        )


def _load_config(config_path: Path) -> dict[str, str]:
    if not config_path.is_absolute():
        raise _fail("公司 TEST 迁移配置必须使用绝对路径。")
    try:
        path_stat = config_path.lstat()
    except OSError:
        raise _fail("公司 TEST 迁移配置不存在或无法读取。") from None
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise _fail("公司 TEST 迁移配置必须是普通文件，禁止符号链接。")
    if stat.S_IMODE(path_stat.st_mode) != 0o600:
        raise _fail("公司 TEST 迁移配置权限必须精确为 600。")

    resolved_path = config_path.parent.resolve() / config_path.name
    try:
        resolved_path.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise _fail("公司 TEST 迁移配置必须放在 Git/镜像工作区之外。")

    values: dict[str, str] = {}
    try:
        lines = resolved_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail("公司 TEST 迁移配置无法按 UTF-8 读取。") from None
    for line in lines:
        line = line.removesuffix("\r")
        if not line or line.lstrip().startswith("#"):
            continue
        if not re.fullmatch(r"[A-Z0-9_]+=.*", line):
            raise _fail("公司 TEST 迁移配置含非法行；只接受 KEY=VALUE。")
        key, value = line.split("=", 1)
        if key not in CONFIG_KEYS:
            raise _fail(f"公司 TEST 迁移配置含未批准字段：{key}。")
        if key in values:
            raise _fail(f"公司 TEST 迁移配置字段重复：{key}。")
        if not value:
            raise _fail(f"公司 TEST 迁移配置项 {key} 不能为空。")
        values[key] = value

    missing = sorted(CONFIG_KEYS - values.keys())
    if missing:
        raise _fail("公司 TEST 迁移配置缺少字段：" + ", ".join(missing))
    expected_identity = {
        "TIDE_ADMIN_DB_HOST": APPROVED_TEST_HOST,
        "TIDE_ADMIN_DB_PORT": APPROVED_TEST_PORT,
        "TIDE_ADMIN_DB_USER": APPROVED_OWNER_ROLE,
        "TIDE_ADMIN_DB_NAME": APPROVED_TEST_DATABASE,
        "TIDE_ADMIN_DB_SSLMODE": APPROVED_SSLMODE,
    }
    if any(values[key] != expected for key, expected in expected_identity.items()):
        raise _fail(
            "公司 TEST 迁移只允许已批准的 tit_growth_test_v2 / postgres 测试目标。"
        )
    return values


def _parse_assignment(source: str, name: str) -> str:
    match = re.search(
        rf'^{re.escape(name)}:[^=\n]+\s*=\s*"([^"]+)"$',
        source,
        flags=re.MULTILINE,
    )
    if match is None:
        raise _fail(f"无法验证 public Alembic 文件中的 {name}。")
    return match.group(1)


def _validate_public_revision_chain() -> None:
    versions = BACKEND_ROOT / "migrations" / "versions"
    for filename, revision, down_revision in PUBLIC_REVISION_CHAIN:
        path = versions / filename
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise _fail(f"缺少或无法读取 public Alembic 文件：{filename}。") from None
        if _parse_assignment(source, "revision") != revision:
            raise _fail(f"public Alembic revision 漂移：{filename}。")
        if _parse_assignment(source, "down_revision") != down_revision:
            raise _fail(f"public Alembic down_revision 漂移：{filename}。")


def _canonical_teacher_ledger() -> tuple[tuple[int, str, str, str], ...]:
    try:
        migrator = TEACHER_MIGRATOR.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise _fail("缺少或无法读取 canonical teacher 迁移器。") from None
    match = re.search(
        r"^PRODUCTION_MIGRATIONS=\(\n(?P<body>.*?)^\)$",
        migrator,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise _fail("无法从 canonical teacher 迁移器读取生产清单。")
    migration_ids = tuple(
        line.strip()
        for line in match.group("body").splitlines()
        if re.fullmatch(r"[0-9]{4}_[a-z0-9_]+", line.strip())
    )
    if not migration_ids or migration_ids[-1] != TEACHER_42:
        raise _fail("canonical teacher 迁移清单未精确结束于 0042。")

    ledger: list[tuple[int, str, str, str]] = []
    for order, migration_id in enumerate(migration_ids, start=1):
        filename = f"{migration_id}.up.sql"
        migration_path = TEACHER_MIGRATIONS / filename
        try:
            payload = migration_path.read_bytes()
        except OSError:
            raise _fail(f"缺少 canonical teacher 迁移文件：{filename}。") from None
        lines = payload.splitlines()
        if not lines or lines[0] != b"BEGIN;" or lines[-1] != b"COMMIT;":
            raise _fail(f"canonical teacher 迁移不是单一事务：{filename}。")
        ledger.append(
            (order, migration_id, filename, hashlib.sha256(payload).hexdigest())
        )
    return tuple(ledger)


def _database_environment(config: Mapping[str, str]) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in DISALLOWED_LIBPQ_ENV
    }
    environment.update(
        {
            "PGPASSWORD": config["TIDE_ADMIN_DB_PASSWORD"],
            "PGCONNECT_TIMEOUT": "8",
            "PGSSLMODE": APPROVED_SSLMODE,
        }
    )
    return environment


def _psql_command(config: Mapping[str, str]) -> list[str]:
    return [
        "psql",
        "-X",
        "--no-password",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        config["TIDE_ADMIN_DB_HOST"],
        "-p",
        config["TIDE_ADMIN_DB_PORT"],
        "-U",
        config["TIDE_ADMIN_DB_USER"],
        "-d",
        config["TIDE_ADMIN_DB_NAME"],
        "-AtF",
        "|",
    ]


def _run_psql(config: Mapping[str, str], sql: str) -> str:
    try:
        result = subprocess.run(
            [*_psql_command(config), "-c", sql],
            check=True,
            capture_output=True,
            text=True,
            env=_database_environment(config),
        )
    except (OSError, subprocess.CalledProcessError):
        raise _fail(
            "公司 TEST 数据库只读状态检查失败；未输出凭据，也未继续迁移。"
        ) from None
    return result.stdout.strip()


def _validate_live_identity(config: Mapping[str, str]) -> None:
    identity = _run_psql(
        config,
        """
        select
          current_database(),
          current_user,
          session_user,
          current_setting('server_version_num')::integer >= 160000,
          coalesce(
            (select ssl from pg_stat_ssl where pid = pg_backend_pid()),
            false
          ),
          (select rolsuper from pg_roles where rolname = current_user)
        """,
    )
    expected = (
        f"{APPROVED_TEST_DATABASE}|{APPROVED_OWNER_ROLE}|"
        f"{APPROVED_OWNER_ROLE}|t|f|t"
    )
    if identity != expected:
        raise _fail(
            "公司 TEST 实际连接身份、PostgreSQL 版本、TLS 状态或 owner 权限不符合批准目标。"
        )


def _public_head(config: Mapping[str, str]) -> str:
    ledger_exists = _run_psql(
        config,
        "select to_regclass('public.alembic_version') is not null",
    )
    if ledger_exists != "t":
        raise _fail("公司 TEST 缺少 public Alembic 账本。")
    head = _run_psql(
        config,
        """
        select case when count(*) = 1 then min(version_num) else '' end
        from public.alembic_version
        """,
    )
    if not head:
        raise _fail("公司 TEST public Alembic 账本不是唯一 Head。")
    return head


def _teacher_head(
    config: Mapping[str, str],
    canonical_ledger: Sequence[tuple[int, str, str, str]],
) -> str:
    ledger_exists = _run_psql(
        config,
        "select to_regclass('tide.schema_migrations') is not null",
    )
    if ledger_exists != "t":
        raise _fail("公司 TEST 缺少 canonical Tide 迁移账本。")
    manifest = _run_psql(
        config,
        """
        select migration_order, migration_id, filename, sha256
        from tide.schema_migrations
        order by migration_order
        """,
    )
    rows: list[tuple[int, str, str, str]] = []
    for line in manifest.splitlines():
        fields = line.split("|")
        if len(fields) != 4:
            raise _fail("公司 TEST Tide 账本格式非法。")
        try:
            order = int(fields[0])
        except ValueError:
            raise _fail("公司 TEST Tide 账本顺序非法。") from None
        rows.append((order, fields[1], fields[2], fields[3]))
    if not rows:
        raise _fail("公司 TEST Tide 账本为空。")
    expected_prefix = tuple(canonical_ledger[: len(rows)])
    if tuple(rows) != expected_prefix:
        raise _fail(
            "公司 TEST Tide 账本不是当前 canonical 清单的精确连续前缀，"
            "或文件名/SHA-256 已漂移。"
        )
    return rows[-1][1]


def _observe_state(
    config: Mapping[str, str],
    canonical_ledger: Sequence[tuple[int, str, str, str]],
) -> DatabaseState:
    _validate_live_identity(config)
    return DatabaseState(
        public_head=_public_head(config),
        teacher_head=_teacher_head(config, canonical_ledger),
    )


def _upgrade_plan(start: DatabaseState) -> tuple[UpgradeAction, ...]:
    if start == FINAL_STATE:
        return ()
    plan: list[UpgradeAction] = []
    state = start
    visited: set[DatabaseState] = set()
    while state != FINAL_STATE:
        if state in visited:
            raise _fail("公司 TEST 分阶段迁移状态机出现循环。")
        visited.add(state)
        action = TRANSITIONS.get(state)
        if action is None:
            raise _fail(
                "当前 public/teacher 组合不在批准的可恢复切换点："
                f"public={state.public_head}, teacher={state.teacher_head}。"
                "未执行任何迁移。"
            )
        plan.append(action)
        state = action.expected_state
    return tuple(plan)


def _run_public_upgrade(
    config: Mapping[str, str],
    target: str,
) -> None:
    environment = _database_environment(config)
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": (
                "postgresql+psycopg://"
                f"{APPROVED_OWNER_ROLE}@{APPROVED_TEST_HOST}:"
                f"{APPROVED_TEST_PORT}/{APPROVED_TEST_DATABASE}"
                "?sslmode=disable&connect_timeout=8"
            ),
            "TIT_MIGRATION_EXPECTED_DATABASE": APPROVED_TEST_DATABASE,
        }
    )
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", target],
            cwd=BACKEND_ROOT,
            env=environment,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise _fail(
            f"public Alembic 分阶段迁移到 {target} 失败；"
            "未输出凭据，请修复后重新运行默认只读检查。"
        ) from None


def _run_teacher_upgrade(
    config: Mapping[str, str],
    config_path: Path,
    target: str,
) -> None:
    environment = _database_environment(config)
    environment.update(
        {
            "TIDE_COMPANY_TEST_MIGRATION_MODE": "true",
            "TIDE_MIGRATION_TEST_MODE": "false",
            "TIDE_MIGRATION_EXPECTED_DATABASE": APPROVED_TEST_DATABASE,
            "TIDE_MIGRATION_TARGET": target,
            "TIDE_COMPANY_TEST_CONFIG_FILE": str(config_path),
            "TIDE_COMPANY_TEST_DB_HOST": APPROVED_TEST_HOST,
            "TIDE_COMPANY_TEST_DB_PORT": APPROVED_TEST_PORT,
            "TIDE_COMPANY_TEST_DB_USER": APPROVED_OWNER_ROLE,
            "TIDE_COMPANY_TEST_DB_NAME": APPROVED_TEST_DATABASE,
            "TIDE_COMPANY_TEST_DB_SSLMODE": APPROVED_SSLMODE,
        }
    )
    try:
        subprocess.run(
            [str(TEACHER_MIGRATOR)],
            cwd=TEACHER_DATABASE_ROOT,
            env=environment,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise _fail(
            f"teacher canonical 分阶段迁移到 {target} 失败；"
            "未输出凭据，请修复后重新运行默认只读检查。"
        ) from None


def _apply_action(
    config: Mapping[str, str],
    config_path: Path,
    action: UpgradeAction,
) -> None:
    if action.schema == "public":
        _run_public_upgrade(config, action.target)
    elif action.schema == "teacher":
        _run_teacher_upgrade(config, config_path, action.target)
    else:
        raise _fail(f"未知迁移 Schema：{action.schema}。")


def _format_state(state: DatabaseState) -> str:
    return f"public={state.public_head} / teacher={state.teacher_head}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "检查或按批准的跨 Schema 顺序升级 tit_growth_test_v2；"
            "默认只检查，只有 --apply 会写数据库。"
        )
    )
    parser.add_argument("config_file", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="显式执行分阶段迁移；省略时只输出只读计划",
    )
    parser.add_argument(
        "--backup-confirmed",
        action="store_true",
        help="确认已完成可恢复备份；仅与 --apply 一起使用",
    )
    parser.add_argument(
        "--maintenance-window-confirmed",
        action="store_true",
        help="确认 API/Worker/其他迁移器已退出写入窗口；仅与 --apply 一起使用",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.apply and not (
        args.backup_confirmed and args.maintenance_window_confirmed
    ):
        raise _fail(
            "--apply 必须同时提供 --backup-confirmed 和 "
            "--maintenance-window-confirmed；未连接数据库。"
        )
    if not args.apply and (
        args.backup_confirmed or args.maintenance_window_confirmed
    ):
        raise _fail("备份/维护窗口确认参数只能与 --apply 一起使用。")
    _reject_ambient_libpq_environment()
    config_path = args.config_file
    config = _load_config(config_path)
    _validate_public_revision_chain()
    canonical_ledger = _canonical_teacher_ledger()
    state = _observe_state(config, canonical_ledger)
    plan = _upgrade_plan(state)

    print(f"当前公司 TEST 状态：{_format_state(state)}")
    if not plan:
        print("公司 TEST 已位于批准的最终状态；未执行任何迁移。")
        return 0
    print("批准的待执行顺序：")
    for index, action in enumerate(plan, start=1):
        print(f"  {index}. {action.schema} -> {action.target}")
    if not args.apply:
        print(
            "只读检查完成；未执行任何迁移。确认备份和维护窗口后追加 "
            "--apply --backup-confirmed --maintenance-window-confirmed。"
        )
        return 0

    for index, action in enumerate(plan, start=1):
        print(f"执行第 {index}/{len(plan)} 阶段：{action.schema} -> {action.target}")
        _apply_action(config, config_path, action)
        observed = _observe_state(config, canonical_ledger)
        if observed != action.expected_state:
            raise _fail(
                "阶段读回不一致："
                f"期望 {_format_state(action.expected_state)}，"
                f"实际 {_format_state(observed)}。已停止后续迁移。"
            )
        print(f"阶段读回通过：{_format_state(observed)}")

    print(
        "公司 TEST 分阶段迁移完成并读回到 public 65 / teacher 0042。"
        "尚未执行 apply-company-test.sh 初始化，也未发布或重启应用。"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
