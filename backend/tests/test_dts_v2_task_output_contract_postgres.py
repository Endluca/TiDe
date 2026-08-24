from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_dts_v2_ops_case_postgres import (
    _postgres_tools_available,
    ops_case_postgres,
)


@pytest.mark.skipif(
    not _postgres_tools_available(),
    reason="local PostgreSQL binaries are required for rev91 contracts",
)
def test_task_output_commands_and_existing_application_crud_are_present(
    ops_case_postgres,
) -> None:
    admin, outbox, _recovery = ops_case_postgres
    with admin.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT
                  to_regprocedure(
                    'public.reconcile_course_trigger_matches_v2('
                    'text,text,bigint,bigint,text,jsonb,text)'
                  ) IS NOT NULL,
                  to_regprocedure(
                    'public.reconcile_blacklist_threshold_v2('
                    'text,text,bigint,bigint,text,jsonb)'
                  ) IS NOT NULL,
                  to_regprocedure(
                    'public.materialize_task_plan_v2('
                    'text,text,bigint,text)'
                  ) IS NOT NULL,
                  has_function_privilege(
                    'tit_growth_app',
                    'public.materialize_task_plan_v2('
                    'text,text,bigint,text)','EXECUTE'
                  ),
                  has_table_privilege(
                    'tit_growth_app',
                    'public.personalized_trigger_matches','INSERT'
                  ),
                  has_table_privilege(
                    'tit_growth_app',
                    'public.task_assignments','INSERT'
                  )
                """
            )
        ).one() == (True, True, True, True, True, True)
