from __future__ import annotations

import pytest
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.dts_v2_runtime_guard import PostgresDtsV2PrimaryTransactionGuard
from test_dts_v2_ops_case_postgres import _run_alembic, ops_case_postgres


def test_guard_uses_runtime_roles_without_raw_control_select(
    ops_case_postgres,
) -> None:
    admin, outbox, _recovery = ops_case_postgres
    domain = create_engine(
        admin.url.set(username="tit_dts_domain_projector_runtime")
    )
    try:
        with admin.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "20260823_100_scope_snapshot_diff"
        with admin.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE public.dts_pipeline_control
                    SET projection_generation=1,row_version=row_version+1,
                        changed_at=transaction_timestamp(),
                        changed_by='RUNTIME_GUARD_TEST'
                    WHERE control_id='PRIMARY' AND mode='V2_PRIMARY'
                    """
                )
            )

        with domain.begin() as connection:
            assert PostgresDtsV2PrimaryTransactionGuard().acquire(
                connection, component="DOMAIN"
            ) == 1
            assert connection.execute(
                text("SELECT current_setting('tit.dts_v2_mode')")
            ).scalar_one() == "V2_PRIMARY"
            assert connection.execute(
                text(
                    "SELECT has_table_privilege(current_user,"
                    "'public.dts_dirty_key_inputs','SELECT')"
                )
            ).scalar_one() is True
            assert connection.execute(
                text(
                    "SELECT has_function_privilege(current_user,"
                    "'public.claim_domain_dirty_keys_v2(text,integer,integer)',"
                    "'EXECUTE')"
                )
            ).scalar_one() is True

        with outbox.begin() as connection:
            assert PostgresDtsV2PrimaryTransactionGuard().acquire(
                connection, component="OUTBOX"
            ) == 1

        for engine in (domain, outbox):
            with pytest.raises(DBAPIError, match="permission denied"):
                with engine.begin() as connection:
                    connection.execute(
                        text("SELECT mode FROM public.dts_pipeline_control")
                    ).all()

        for mode in ("ROLLED_BACK", "V1_COMPAT_DUAL_CAPTURE"):
            with admin.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.dts_pipeline_control
                        SET mode=:mode,row_version=row_version+1,
                            changed_at=transaction_timestamp(),
                            changed_by='RUNTIME_GUARD_SHADOW_TEST'
                        WHERE control_id='PRIMARY'
                        """
                    ),
                    {"mode": mode},
                )
            with domain.begin() as connection:
                assert PostgresDtsV2PrimaryTransactionGuard().acquire(
                    connection, component="DOMAIN"
                ) == 1
                assert connection.execute(
                    text("SELECT current_setting('tit.dts_v2_mode')")
                ).scalar_one() == mode
            with outbox.begin() as connection:
                assert (
                    PostgresDtsV2PrimaryTransactionGuard().acquire(
                        connection, component="OUTBOX"
                    )
                    is None
                )
    finally:
        domain.dispose()
