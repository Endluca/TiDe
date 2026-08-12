from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from scripts import upgrade_company_test_database as upgrade


SAFE_CONFIG = {
    "TIDE_ADMIN_DB_HOST": upgrade.APPROVED_TEST_HOST,
    "TIDE_ADMIN_DB_PORT": upgrade.APPROVED_TEST_PORT,
    "TIDE_ADMIN_DB_USER": upgrade.APPROVED_OWNER_ROLE,
    "TIDE_ADMIN_DB_NAME": upgrade.APPROVED_TEST_DATABASE,
    "TIDE_ADMIN_DB_PASSWORD": "do-not-print=this-secret",
    "TIDE_ADMIN_DB_SSLMODE": upgrade.APPROVED_SSLMODE,
}


def _write_config(tmp_path: Path, **overrides: str) -> Path:
    values = SAFE_CONFIG | overrides
    path = tmp_path / "company-test.env"
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_full_plan_preserves_every_cross_schema_switch_point() -> None:
    plan = upgrade._upgrade_plan(
        upgrade.DatabaseState(upgrade.PUBLIC_50, upgrade.TEACHER_32)
    )

    assert [(action.schema, action.target) for action in plan] == [
        ("public", upgrade.PUBLIC_54),
        ("teacher", upgrade.TEACHER_37),
        ("public", upgrade.PUBLIC_55),
        ("public", upgrade.PUBLIC_56),
        ("teacher", upgrade.TEACHER_38),
        ("public", upgrade.PUBLIC_57),
        ("teacher", upgrade.TEACHER_40),
        ("teacher", upgrade.TEACHER_41),
    ]
    assert plan[-1].expected_state == upgrade.FINAL_STATE


@pytest.mark.parametrize(
    ("start", "first_target"),
    [
        (
            upgrade.DatabaseState(upgrade.PUBLIC_51, upgrade.TEACHER_32),
            upgrade.PUBLIC_54,
        ),
        (
            upgrade.DatabaseState(upgrade.PUBLIC_54, upgrade.TEACHER_33),
            upgrade.TEACHER_37,
        ),
        (
            upgrade.DatabaseState(upgrade.PUBLIC_57, upgrade.TEACHER_39),
            upgrade.TEACHER_40,
        ),
    ],
)
def test_plan_resumes_only_from_approved_partial_stage(
    start: upgrade.DatabaseState,
    first_target: str,
) -> None:
    assert upgrade._upgrade_plan(start)[0].target == first_target


def test_plan_fails_closed_on_wrong_cross_schema_order() -> None:
    with pytest.raises(RuntimeError, match="不在批准的可恢复切换点"):
        upgrade._upgrade_plan(
            upgrade.DatabaseState(upgrade.PUBLIC_57, upgrade.TEACHER_37)
        )


def test_config_is_locked_to_external_mode_600_approved_target(
    tmp_path: Path,
) -> None:
    path = _write_config(tmp_path)
    assert upgrade._load_config(path) == SAFE_CONFIG

    path.chmod(0o640)
    with pytest.raises(RuntimeError, match="权限必须精确为 600"):
        upgrade._load_config(path)

    path = _write_config(tmp_path, TIDE_ADMIN_DB_NAME="postgres")
    with pytest.raises(RuntimeError, match="只允许已批准") as exc_info:
        upgrade._load_config(path)
    assert SAFE_CONFIG["TIDE_ADMIN_DB_PASSWORD"] not in str(exc_info.value)


def test_local_public_and_teacher_chains_are_exactly_validated() -> None:
    upgrade._validate_public_revision_chain()
    ledger = upgrade._canonical_teacher_ledger()

    assert ledger[0][0:3] == (1, "0001_initial", "0001_initial.up.sql")
    assert ledger[-1][0:3] == (
        36,
        upgrade.TEACHER_41,
        f"{upgrade.TEACHER_41}.up.sql",
    )
    assert all(len(row[3]) == 64 for row in ledger)


def test_default_main_is_read_only_and_prints_the_plan(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    start = upgrade.DatabaseState(upgrade.PUBLIC_50, upgrade.TEACHER_32)
    monkeypatch.setattr(upgrade, "_reject_ambient_libpq_environment", lambda: None)
    monkeypatch.setattr(upgrade, "_load_config", lambda _path: SAFE_CONFIG)
    monkeypatch.setattr(upgrade, "_validate_public_revision_chain", lambda: None)
    monkeypatch.setattr(upgrade, "_canonical_teacher_ledger", lambda: ())
    monkeypatch.setattr(upgrade, "_observe_state", lambda *_args: start)
    monkeypatch.setattr(
        upgrade,
        "_apply_action",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("check mode must not apply migrations")
        ),
    )

    assert upgrade.main(["/tmp/company-test.env"]) == 0

    output = capsys.readouterr().out
    assert "只读检查完成" in output
    assert upgrade.PUBLIC_54 in output
    assert upgrade.TEACHER_41 in output


def test_apply_requires_backup_and_maintenance_confirmations_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        upgrade,
        "_reject_ambient_libpq_environment",
        lambda: (_ for _ in ()).throw(
            AssertionError("confirmation gate must run before environment checks")
        ),
    )

    with pytest.raises(RuntimeError, match="未连接数据库"):
        upgrade.main(["/tmp/company-test.env", "--apply"])


def test_apply_reads_back_every_stage_before_continuing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = upgrade.DatabaseState(upgrade.PUBLIC_50, upgrade.TEACHER_32)
    plan = upgrade._upgrade_plan(start)
    observed = iter([start, *(action.expected_state for action in plan)])
    applied: list[tuple[str, str]] = []
    monkeypatch.setattr(upgrade, "_reject_ambient_libpq_environment", lambda: None)
    monkeypatch.setattr(upgrade, "_load_config", lambda _path: SAFE_CONFIG)
    monkeypatch.setattr(upgrade, "_validate_public_revision_chain", lambda: None)
    monkeypatch.setattr(upgrade, "_canonical_teacher_ledger", lambda: ())
    monkeypatch.setattr(upgrade, "_observe_state", lambda *_args: next(observed))
    monkeypatch.setattr(
        upgrade,
        "_apply_action",
        lambda _config, _path, action: applied.append(
            (action.schema, action.target)
        ),
    )

    assert (
        upgrade.main(
            [
                "/tmp/company-test.env",
                "--apply",
                "--backup-confirmed",
                "--maintenance-window-confirmed",
            ]
        )
        == 0
    )
    assert applied == [(action.schema, action.target) for action in plan]


def test_public_upgrade_keeps_password_out_of_command_and_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> None:
        captured["command"] = command
        captured.update(kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    upgrade._run_public_upgrade(SAFE_CONFIG, upgrade.PUBLIC_54)

    command = captured["command"]
    environment = captured["env"]
    assert isinstance(command, list)
    assert isinstance(environment, dict)
    assert command[-2:] == ["upgrade", upgrade.PUBLIC_54]
    assert SAFE_CONFIG["TIDE_ADMIN_DB_PASSWORD"] not in " ".join(command)
    assert SAFE_CONFIG["TIDE_ADMIN_DB_PASSWORD"] not in environment["DATABASE_URL"]
    assert environment["PGPASSWORD"] == SAFE_CONFIG["TIDE_ADMIN_DB_PASSWORD"]


def test_company_test_teacher_mode_allows_only_reviewed_stage_targets() -> None:
    source = upgrade.TEACHER_MIGRATOR.read_text(encoding="utf-8")

    approved_block = source.split("APPROVED_COMPANY_TEST_TARGETS=(", 1)[1].split(
        "\n)", 1
    )[0]
    assert tuple(approved_block.split()) == (
        upgrade.TEACHER_37,
        upgrade.TEACHER_38,
        upgrade.TEACHER_40,
        upgrade.TEACHER_41,
    )
    assert "approved_company_test_target=false" in source
    assert "0037、0038、0040、0041 切换点" in source
