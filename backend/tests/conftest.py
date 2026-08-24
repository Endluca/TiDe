from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path


_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="tit-growth-tests-"))
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_TEST_DB_DIR / 'test.db'}"
os.environ["APP_ENV"] = "test"

# The disposable test harness explicitly owns schema creation. Runtime startup
# never calls create_all and therefore cannot bypass Alembic.
from app import auth_models, config_models, db_models  # noqa: E402,F401
from app.auth import OperatorIdentity, current_operator  # noqa: E402
from app.auth_models import OperatorRole  # noqa: E402
from app.database import Base, engine  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture
def synthetic_v2_appoint_profiles(monkeypatch):
    """Install test-only appoint profiles for inert v2 algorithm tests."""

    from dts_v2_test_profiles import install_synthetic_appoint_profiles

    return install_synthetic_appoint_profiles(monkeypatch)


# ``task_assignments`` deliberately uses PostgreSQL-only server defaults in
# production.  The disposable unit-test harness remains SQLite so the broader
# scoring/import/auth suite stays fast.  Tests always provide these values
# explicitly; removing only the incompatible defaults keeps the production
# model untouched while still exercising its constraints and relationships.
for _column_name in (
    "assignment_id",
    "created_by",
    "updated_by",
    "assigned_at",
    "status_changed_at",
    "created_at",
    "updated_at",
):
    db_models.TaskAssignmentRecord.__table__.c[_column_name].server_default = None

# SQLite refuses to create a CHECK that calls PostgreSQL's ``btrim`` even if
# the table stays empty.  The authoritative PostgreSQL migration/permission
# test covers that guard; all portable assignment checks remain active here.
_assignment_table = db_models.TaskAssignmentRecord.__table__
_postgres_text_guard = next(
    constraint
    for constraint in _assignment_table.constraints
    if constraint.name == "ck_task_assignment_required_text"
)
_assignment_table.constraints.remove(_postgres_text_guard)

Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def _destructive_test_domain_reset():
    """Give every test a pristine disposable domain database.

    Production code must not own a domain-wide deletion path.  This disposable
    SQLite fixture does: clear every mapped table before rebuilding the small
    test-only working set and task catalog.
    """

    from app.database import session_scope
    from app.store import store
    from app.task_seed import seed_task_catalog

    with session_scope(engine) as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
    store.reset()
    first_seed = seed_task_catalog(engine)
    repeated_seed = seed_task_catalog(engine)
    assert first_seed["template_catalog_size"] == 14
    assert repeated_seed["templates_created"] == 0
    yield


@pytest.fixture(autouse=True)
def _authenticated_main_app():
    """Existing domain contract tests run as a fully authorized test operator."""

    from app.main import app

    identity = OperatorIdentity(
        operator_id="test-operator",
        username="test.operator",
        display_name="Test Operator",
        roles=list(OperatorRole),
    )
    app.dependency_overrides[current_operator] = lambda: identity
    yield
    app.dependency_overrides.pop(current_operator, None)


@atexit.register
def _cleanup_test_database() -> None:
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)
