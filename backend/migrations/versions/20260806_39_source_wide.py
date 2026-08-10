"""add the two current source-wide tables and lightweight change events

Revision ID: 20260806_39_source_wide
Revises: 20260729_38_catalog_scores
Create Date: 2026-08-06

The upstream monitoring service owns these two current-state source tables.
They contain only the supplied CSV columns.  Row triggers publish a compact
field-diff event; all business calculation remains outside PostgreSQL triggers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260806_39_source_wide"
down_revision: Union[str, None] = "20260729_38_catalog_scores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # Cluster roles are platform-owned and deliberately not created by an
    # application migration.  Missing roles must fail closed so a successful
    # revision can never silently omit the source-table ACL contract.
    op.execute(
        """
        DO $source_wide_required_roles$
        DECLARE
            monitor_can_login boolean;
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app'
            ) THEN
                RAISE EXCEPTION
                    'required database role tit_growth_app does not exist';
            END IF;

            SELECT rolcanlogin
            INTO monitor_can_login
            FROM pg_roles
            WHERE rolname = 'tit_source_monitor';

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'required database role tit_source_monitor does not exist';
            END IF;
            IF monitor_can_login THEN
                RAISE EXCEPTION
                    'tit_source_monitor must be a NOLOGIN permission group';
            END IF;
        END
        $source_wide_required_roles$;
        """
    )

    op.create_table(
        "teacher_source_wide",
        sa.Column("tchr_id", sa.String(length=64), primary_key=True),
        sa.Column("real_name", sa.Text(), nullable=True),
        sa.Column("tchr_group", sa.Text(), nullable=True),
        sa.Column("tchr_group_desc", sa.Text(), nullable=True),
        sa.Column("center_type_id", sa.Text(), nullable=True),
        sa.Column("center_type_desc", sa.Text(), nullable=True),
        sa.Column("bu", sa.Text(), nullable=True),
        sa.Column("based_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("status_on_date", sa.Date(), nullable=True),
        sa.Column("status_off_date", sa.Date(), nullable=True),
        sa.Column("last_on_date", sa.Date(), nullable=True),
        sa.Column("job_days", sa.Integer(), nullable=True),
        sa.Column("job_month", sa.Float(), nullable=True),
        sa.Column("is_ft_hbt", sa.Boolean(), nullable=True),
        sa.Column("is_fte", sa.Boolean(), nullable=True),
        sa.Column("teach_area_type", sa.Text(), nullable=True),
        sa.Column("tchr_score", sa.Float(), nullable=True),
        sa.Column("onboard_date", sa.Date(), nullable=True),
        sa.Column("onboard_30d_end_date", sa.Date(), nullable=True),
        sa.Column(
            "first_open_slot_dt",
            sa.Date(),
            nullable=True,
        ),
        sa.Column(
            "first_booked_dt",
            sa.Date(),
            nullable=True,
        ),
        sa.Column(
            "first_completed_dt",
            sa.Date(),
            nullable=True,
        ),
        sa.Column("total_booked_cnt", sa.Integer(), nullable=True),
        sa.Column("peak_booked_cnt", sa.Integer(), nullable=True),
        sa.Column("total_completed_cnt", sa.Integer(), nullable=True),
        sa.Column("peak_completed_cnt", sa.Integer(), nullable=True),
        sa.Column("absent_cnt", sa.Integer(), nullable=True),
        sa.Column("late_cnt", sa.Integer(), nullable=True),
        sa.Column("early_cnt", sa.Integer(), nullable=True),
        sa.Column("anomaly_cnt", sa.Integer(), nullable=True),
        sa.Column("perfect_cnt", sa.Integer(), nullable=True),
        sa.Column("no_notice_cnt", sa.Integer(), nullable=True),
        sa.Column("first_completed_student_cnt", sa.Integer(), nullable=True),
        sa.Column(
            "completed_again_student_15d_cnt",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("feedback_total_eval_cnt", sa.Integer(), nullable=True),
        sa.Column("feedback_praise_cnt", sa.Integer(), nullable=True),
        sa.Column("feedback_negative_cnt", sa.Integer(), nullable=True),
        sa.Column("feedback_complaint_cnt", sa.Integer(), nullable=True),
        sa.Column("feedback_valid_complaint_cnt", sa.Integer(), nullable=True),
        sa.Column("feedback_favorite_cnt", sa.Integer(), nullable=True),
        sa.Column("feedback_block_cnt", sa.Integer(), nullable=True),
        sa.Column("total_slot_cnt", sa.Integer(), nullable=True),
        sa.Column("reg_slot_cnt", sa.Integer(), nullable=True),
        sa.Column("peak_slot_cnt", sa.Integer(), nullable=True),
        sa.Column("slot_days", sa.Integer(), nullable=True),
        sa.Column("peak_slot_days", sa.Integer(), nullable=True),
        sa.Column("reliability_absent_rate", sa.Float(), nullable=True),
        sa.Column("reliability_late_rate", sa.Float(), nullable=True),
        sa.Column("reliability_early_leave_rate", sa.Float(), nullable=True),
        sa.Column("reliability_late_early_rate", sa.Float(), nullable=True),
        sa.Column("feedback_praise_rate", sa.Float(), nullable=True),
        sa.Column("feedback_negative_rate", sa.Float(), nullable=True),
        sa.Column("feedback_complaint_rate", sa.Float(), nullable=True),
        sa.Column("feedback_rebook_rate", sa.Float(), nullable=True),
        sa.Column("feedback_favorite_rate", sa.Float(), nullable=True),
        sa.Column("feedback_block_rate", sa.Float(), nullable=True),
        sa.Column("feedback_eval_rate", sa.Float(), nullable=True),
        sa.Column("capacity_avg_completed_per_day", sa.Float(), nullable=True),
        sa.Column("capacity_peak_slot_rate", sa.Float(), nullable=True),
        sa.Column("capacity_key_slot_day_rate", sa.Float(), nullable=True),
        schema="public",
    )

    op.create_table(
        "lesson_source_wide",
        sa.Column("课程id", sa.String(length=128), primary_key=True),
        sa.Column("上课日期", sa.Date(), nullable=True),
        sa.Column("上课时间", sa.Time(), nullable=True),
        sa.Column("是否高峰", sa.Boolean(), nullable=True),
        sa.Column("老师id", sa.String(length=64), nullable=False),
        sa.Column("学员id", sa.String(length=128), nullable=True),
        sa.Column("课程状态", sa.Text(), nullable=True),
        sa.Column("缺席原因明细", sa.Text(), nullable=True),
        sa.Column("迟到", sa.Boolean(), nullable=True),
        sa.Column("早退", sa.Boolean(), nullable=True),
        sa.Column("差评分", sa.Float(), nullable=True),
        sa.Column("差评标签", sa.Boolean(), nullable=True),
        sa.Column("投诉一级分类", sa.Text(), nullable=True),
        sa.Column("投诉二级分类", sa.Text(), nullable=True),
        sa.Column("投诉三级分类", sa.Text(), nullable=True),
        sa.Column("是否拉黑", sa.Boolean(), nullable=True),
        sa.Column("收藏", sa.Boolean(), nullable=True),
        sa.Column("好评标签", sa.Boolean(), nullable=True),
        sa.Column("评价详情", sa.Text(), nullable=True),
        sa.Column("未开摄像头", sa.Boolean(), nullable=True),
        sa.Column("cpu占用过高", sa.Boolean(), nullable=True),
        sa.Column("网络延迟过高", sa.Boolean(), nullable=True),
        sa.Column("假早退", sa.Boolean(), nullable=True),
        schema="public",
    )
    # These are the two established incremental read paths: teacher timeline,
    # and teacher-student ordering for favorite/blacklist rules.
    op.create_index(
        "ix_lesson_source_wide_teacher_time",
        "lesson_source_wide",
        ["老师id", "上课日期", "上课时间"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_lesson_source_wide_teacher_student_time",
        "lesson_source_wide",
        ["老师id", "学员id", "上课日期", "上课时间"],
        unique=False,
        schema="public",
    )

    op.execute(
        """
        REVOKE ALL PRIVILEGES ON TABLE
            public.teacher_source_wide,
            public.lesson_source_wide
        FROM PUBLIC;

        GRANT SELECT ON TABLE
            public.teacher_source_wide,
            public.lesson_source_wide
        TO tit_growth_app;

        GRANT USAGE ON SCHEMA public TO tit_source_monitor;
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            public.teacher_source_wide,
            public.lesson_source_wide
        TO tit_source_monitor;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.emit_source_wide_change_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            old_row jsonb := '{}'::jsonb;
            new_row jsonb := '{}'::jsonb;
            changed_fields text[] := ARRAY[]::text[];
            source_table_name text;
            source_id_value text;
            old_teacher_id_value text;
            new_teacher_id_value text;
            aggregate_type_value text;
            event_token text;
            event_time timestamptz := clock_timestamp();
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                old_row := to_jsonb(OLD);
            END IF;
            IF TG_OP <> 'DELETE' THEN
                new_row := to_jsonb(NEW);
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF TG_TABLE_NAME = 'teacher_source_wide'
                   AND old_row ->> 'tchr_id' IS DISTINCT FROM new_row ->> 'tchr_id' THEN
                    RAISE EXCEPTION 'teacher_source_wide.tchr_id is immutable';
                ELSIF TG_TABLE_NAME = 'lesson_source_wide'
                   AND old_row ->> '课程id' IS DISTINCT FROM new_row ->> '课程id' THEN
                    RAISE EXCEPTION 'lesson_source_wide.课程id is immutable';
                END IF;

                SELECT COALESCE(array_agg(field_name ORDER BY field_name), ARRAY[]::text[])
                INTO changed_fields
                FROM (
                    SELECT key_name AS field_name
                    FROM jsonb_object_keys(old_row || new_row) AS keys(key_name)
                    WHERE old_row -> key_name IS DISTINCT FROM new_row -> key_name
                ) AS changed;

                IF cardinality(changed_fields) = 0 THEN
                    RETURN NEW;
                END IF;
            ELSIF TG_OP = 'INSERT' THEN
                SELECT COALESCE(array_agg(key_name ORDER BY key_name), ARRAY[]::text[])
                INTO changed_fields
                FROM jsonb_object_keys(new_row) AS keys(key_name);
            ELSE
                SELECT COALESCE(array_agg(key_name ORDER BY key_name), ARRAY[]::text[])
                INTO changed_fields
                FROM jsonb_object_keys(old_row) AS keys(key_name);
            END IF;

            source_table_name := TG_TABLE_NAME;
            IF TG_TABLE_NAME = 'teacher_source_wide' THEN
                source_id_value := COALESCE(
                    new_row ->> 'tchr_id',
                    old_row ->> 'tchr_id'
                );
                old_teacher_id_value := old_row ->> 'tchr_id';
                new_teacher_id_value := new_row ->> 'tchr_id';
                aggregate_type_value := 'TEACHER_SOURCE_WIDE';
            ELSIF TG_TABLE_NAME = 'lesson_source_wide' THEN
                source_id_value := COALESCE(
                    new_row ->> '课程id',
                    old_row ->> '课程id'
                );
                old_teacher_id_value := old_row ->> '老师id';
                new_teacher_id_value := new_row ->> '老师id';
                aggregate_type_value := 'LESSON_SOURCE_WIDE';
            ELSE
                RAISE EXCEPTION 'unsupported source-wide table: %', TG_TABLE_NAME;
            END IF;

            event_token := md5(concat_ws(
                '|',
                source_table_name,
                TG_OP,
                source_id_value,
                txid_current()::text,
                event_time::text,
                pg_backend_pid()::text,
                random()::text
            ));

            INSERT INTO public.outbox_events (
                outbox_id,
                event_id,
                aggregate_type,
                aggregate_id,
                event_type,
                payload,
                status,
                available_at,
                attempt_count,
                last_error,
                created_at,
                published_at
            ) VALUES (
                'OUT-SOURCE-' || event_token,
                'EVT-SOURCE-' || event_token,
                aggregate_type_value,
                source_id_value,
                'source_wide.changed.v1',
                jsonb_build_object(
                    'source_table', source_table_name,
                    'source_id', source_id_value,
                    'operation', TG_OP,
                    'changed_fields', changed_fields,
                    'old_teacher_id', old_teacher_id_value,
                    'new_teacher_id', new_teacher_id_value
                ),
                'PENDING',
                event_time,
                0,
                NULL,
                event_time,
                NULL
            );

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $function$;

        REVOKE ALL ON FUNCTION public.emit_source_wide_change_v1() FROM PUBLIC;

        CREATE TRIGGER trg_teacher_source_wide_outbox_v1
        AFTER INSERT OR UPDATE OR DELETE ON public.teacher_source_wide
        FOR EACH ROW
        EXECUTE FUNCTION public.emit_source_wide_change_v1();

        CREATE TRIGGER trg_lesson_source_wide_outbox_v1
        AFTER INSERT OR UPDATE OR DELETE ON public.lesson_source_wide
        FOR EACH ROW
        EXECUTE FUNCTION public.emit_source_wide_change_v1();
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $source_wide_downgrade_guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.teacher_source_wide LIMIT 1)
               OR EXISTS (SELECT 1 FROM public.lesson_source_wide LIMIT 1)
               OR EXISTS (
                   SELECT 1
                   FROM public.outbox_events
                   WHERE event_type = 'source_wide.changed.v1'
                     AND status = 'PENDING'
                   LIMIT 1
               ) THEN
                RAISE EXCEPTION
                    'refusing to drop active source-wide state; clear source rows and process or park pending source events first';
            END IF;
        END
        $source_wide_downgrade_guard$;

        DROP TRIGGER IF EXISTS trg_teacher_source_wide_outbox_v1
            ON public.teacher_source_wide;
        DROP TRIGGER IF EXISTS trg_lesson_source_wide_outbox_v1
            ON public.lesson_source_wide;
        DROP FUNCTION IF EXISTS public.emit_source_wide_change_v1();

        DO $source_wide_acl_rollback$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_growth_app') THEN
                REVOKE SELECT ON TABLE
                    public.teacher_source_wide,
                    public.lesson_source_wide
                FROM tit_growth_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tit_source_monitor') THEN
                REVOKE ALL PRIVILEGES ON TABLE
                    public.teacher_source_wide,
                    public.lesson_source_wide
                FROM tit_source_monitor;
            END IF;
        END
        $source_wide_acl_rollback$;
        """
    )
    op.drop_index(
        "ix_lesson_source_wide_teacher_student_time",
        table_name="lesson_source_wide",
        schema="public",
    )
    op.drop_index(
        "ix_lesson_source_wide_teacher_time",
        table_name="lesson_source_wide",
        schema="public",
    )
    op.drop_table("lesson_source_wide", schema="public")
    op.drop_table("teacher_source_wide", schema="public")
