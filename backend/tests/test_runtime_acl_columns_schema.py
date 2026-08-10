from __future__ import annotations

from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20260806_41_runtime_acl_columns.py"
)


def test_runtime_login_rehash_has_only_its_required_update_columns() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "GRANT UPDATE (updated_at)" in source
    assert "password_hash" in source
    assert "username" in source
    assert "has_table_privilege" in source
    assert "REVOKE UPDATE (updated_at)" in source
    assert "GRANT UPDATE ON TABLE" not in source
