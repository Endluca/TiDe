"""activate deferred guards for the inert course-participation shadow schema.

Revision ID: 20260822_68_course_participation_guards
Revises: 20260822_67_course_part
Create Date: 2026-08-22

The course and participation tables remain shadow-only.  This revision adds
database-side integrity guards only: no runtime route is enabled and no role
receives a table or function privilege.  Every semantic guard is a deferred
constraint trigger so a reducer can replace the current participation and its
course pointer atomically in one transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "20260822_68_course_part_guards"
down_revision: Union[str, None] = "20260822_67_course_part"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


SHADOW_SCHEMA_ONLY = True
DEFERRED_BIDIRECTIONAL_GUARD_IMPLEMENTED = True
DEFERRED_SEMANTIC_GUARDS_IMPLEMENTED = True
DEFERRED_PROVENANCE_GUARD_IMPLEMENTED = True
DEFERRED_COURSE_HISTORY_GUARD_IMPLEMENTED = True
INITIAL_COMPLETION_SNAPSHOT_IMMUTABILITY_IMPLEMENTED = True


def _create_deferred_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION public.dts_v2_course_participation_bidirectional_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            guard_region varchar(8);
            guard_appoint_id varchar(512);
            course_row public.source_courses%ROWTYPE;
            current_count integer;
            current_matches boolean;
            completion_count integer;
            completion_matches boolean;
        BEGIN
            IF TG_TABLE_NAME = 'source_courses' THEN
                guard_region := COALESCE(NEW.source_region, OLD.source_region);
                guard_appoint_id := COALESCE(
                    NEW.source_appoint_id,
                    OLD.source_appoint_id
                );
            ELSE
                guard_region := COALESCE(NEW.source_region, OLD.source_region);
                guard_appoint_id := COALESCE(
                    NEW.source_appoint_id,
                    OLD.source_appoint_id
                );
            END IF;

            SELECT *
            INTO course_row
            FROM public.source_courses
            WHERE source_region = guard_region
              AND source_appoint_id = guard_appoint_id;

            IF NOT FOUND THEN
                IF EXISTS (
                    SELECT 1
                    FROM public.source_course_participations
                    WHERE source_region = guard_region
                      AND source_appoint_id = guard_appoint_id
                      AND (
                          is_current IS TRUE
                          OR participation_role = 'COMPLETION'
                      )
                ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_REVERSE_ORPHAN: course %/% is missing',
                        guard_region,
                        guard_appoint_id;
                END IF;
                RETURN NULL;
            END IF;

            SELECT
                count(*),
                bool_or(
                    participation_seq = course_row.current_participation_seq
                    AND teacher_id = course_row.current_teacher_id
                    AND teacher_id_type = course_row.current_teacher_id_type
                )
            INTO current_count, current_matches
            FROM public.source_course_participations
            WHERE source_region = guard_region
              AND source_appoint_id = guard_appoint_id
              AND is_current IS TRUE;

            IF course_row.current_participation_seq IS NULL THEN
                IF current_count <> 0 THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_CURRENT_REVERSE_ORPHAN: course %/% has no current pointer',
                        guard_region,
                        guard_appoint_id;
                END IF;
            ELSIF current_count <> 1 OR current_matches IS NOT TRUE THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_CURRENT_POINTER_MISMATCH: course %/% pointer is not the unique current participation',
                    guard_region,
                    guard_appoint_id;
            END IF;

            IF course_row.completion_participation_seq IS NOT NULL
               AND course_row.initial_completion_snapshot IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_COMPLETION_SNAPSHOT_REQUIRED: course %/% has a completion pointer without its initial completion snapshot',
                    guard_region,
                    guard_appoint_id;
            END IF;

            SELECT
                count(*),
                bool_or(
                    participation_seq = course_row.completion_participation_seq
                    AND teacher_id = course_row.completion_teacher_id
                    AND teacher_id_type = course_row.completion_teacher_id_type
                )
            INTO completion_count, completion_matches
            FROM public.source_course_participations
            WHERE source_region = guard_region
              AND source_appoint_id = guard_appoint_id
              AND participation_role = 'COMPLETION';

            IF course_row.completion_participation_seq IS NULL THEN
                IF completion_count <> 0 THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_COMPLETION_REVERSE_ORPHAN: course %/% has no completion pointer',
                        guard_region,
                        guard_appoint_id;
                END IF;
            ELSIF completion_count <> 1 OR completion_matches IS NOT TRUE THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_COMPLETION_POINTER_MISMATCH: course %/% pointer is not the unique completion participation',
                    guard_region,
                    guard_appoint_id;
            END IF;

            RETURN NULL;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_course_participation_provenance_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            version_row record;
            assignment_row jsonb;
            assignment_teacher_id text;
            assignment_teacher_type text;
            valid_phase boolean;
        BEGIN
            IF TG_OP = 'UPDATE' AND NEW.row_version <= OLD.row_version THEN
                RAISE EXCEPTION
                    'DTS_V2_PARTICIPATION_ROW_VERSION_NOT_MONOTONIC: participation row_version must strictly increase';
            END IF;

            IF TG_OP = 'UPDATE'
               AND ROW(
                    NEW.source_region,
                    NEW.source_appoint_id,
                    NEW.participation_seq,
                    NEW.teacher_id,
                    NEW.teacher_id_type,
                    NEW.assigned_at,
                    NEW.assigned_at_evidence_status,
                    NEW.assignment_source_partition_epoch_id,
                    NEW.assignment_event_topic,
                    NEW.assignment_event_partition,
                    NEW.assignment_event_offset,
                    NEW.assignment_source_row_revision,
                    NEW.assignment_event_phase
               ) IS DISTINCT FROM ROW(
                    OLD.source_region,
                    OLD.source_appoint_id,
                    OLD.participation_seq,
                    OLD.teacher_id,
                    OLD.teacher_id_type,
                    OLD.assigned_at,
                    OLD.assigned_at_evidence_status,
                    OLD.assignment_source_partition_epoch_id,
                    OLD.assignment_event_topic,
                    OLD.assignment_event_partition,
                    OLD.assignment_event_offset,
                    OLD.assignment_source_row_revision,
                    OLD.assignment_event_phase
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_PARTICIPATION_IDENTITY_IMMUTABLE: participation identity and assignment provenance cannot change';
            END IF;

            IF NEW.participation_role = 'COMPLETION'
               AND NEW.participation_status IS DISTINCT FROM 'end' THEN
                RAISE EXCEPTION
                    'DTS_V2_PARTICIPATION_COMPLETION_STATUS_INVALID: completion participation must have status end';
            END IF;

            -- Reducer-created BEFORE rows intentionally have no assignment time.
            -- Do not require a timestamp for SOURCE_MISSING evidence: a future
            -- producer may know the time but still lack other evidence.
            IF NOT (
                (NEW.assigned_at IS NULL
                 AND NEW.assigned_at_evidence_status = 'SOURCE_MISSING')
                OR (NEW.assigned_at IS NOT NULL
                    AND NEW.assigned_at_evidence_status = 'CONFIRMED')
            ) THEN
                RAISE EXCEPTION
                    'DTS_V2_PARTICIPATION_ASSIGNED_AT_EVIDENCE_INVALID: assignment time and evidence status disagree';
            END IF;

            SELECT
                version_kind,
                source_table,
                source_key,
                source_row_revision,
                operation,
                before_row,
                after_row,
                source_field_types
            INTO version_row
            FROM public.dts_source_row_versions
            WHERE source_region = NEW.source_region
              AND source_partition_epoch_id = NEW.assignment_source_partition_epoch_id
              AND topic = NEW.assignment_event_topic
              AND partition_id = NEW.assignment_event_partition
              AND offset_value = NEW.assignment_event_offset;

            IF NOT FOUND OR version_row.source_table IS DISTINCT FROM
                    format('%s_appoint', NEW.source_region)
               OR version_row.source_key IS DISTINCT FROM NEW.source_appoint_id
               OR version_row.source_row_revision IS DISTINCT FROM
                    NEW.assignment_source_row_revision THEN
                RAISE EXCEPTION
                    'DTS_V2_PARTICIPATION_PROVENANCE_INVALID: participation provenance is not an appoint row version for this course revision';
            END IF;

            valid_phase := false;
            IF NEW.assignment_event_phase = 'BEFORE' THEN
                assignment_row := version_row.before_row;
                valid_phase := (
                    (version_row.version_kind = 'CDC'
                     AND version_row.operation IN ('UPDATE', 'DELETE'))
                    OR (version_row.version_kind = 'SNAPSHOT_DIFF'
                        AND version_row.operation IN (
                            'SNAPSHOT_UPDATE', 'SNAPSHOT_DELETE'
                        ))
                );
            ELSIF NEW.assignment_event_phase = 'AFTER' THEN
                assignment_row := version_row.after_row;
                valid_phase := (
                    version_row.version_kind = 'CDC'
                    AND version_row.operation IN ('INSERT', 'UPDATE')
                );
            ELSIF NEW.assignment_event_phase = 'SNAPSHOT_DIFF' THEN
                assignment_row := version_row.after_row;
                valid_phase := (
                    version_row.version_kind = 'SNAPSHOT_DIFF'
                    AND version_row.operation IN (
                        'SNAPSHOT_INSERT', 'SNAPSHOT_UPDATE',
                        'SNAPSHOT_BOOTSTRAP_PRESENT'
                    )
                );
            END IF;

            IF NOT valid_phase OR assignment_row IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_PARTICIPATION_PROVENANCE_INVALID: assignment phase has no authoritative appoint image';
            END IF;

            assignment_teacher_type :=
                version_row.source_field_types ->> 't_id';
            IF assignment_teacher_type = 'TEXT'
               AND jsonb_typeof(assignment_row -> 't_id') = 'string' THEN
                assignment_teacher_id := assignment_row ->> 't_id';
            ELSIF assignment_teacher_type = 'NUMERIC'
                  AND jsonb_typeof(assignment_row -> 't_id') IN (
                      'number', 'string'
                  ) THEN
                BEGIN
                    assignment_teacher_id := trim_scale(
                        (assignment_row ->> 't_id')::numeric
                    )::text;
                EXCEPTION WHEN invalid_text_representation
                               OR numeric_value_out_of_range THEN
                    assignment_teacher_id := NULL;
                END;
            ELSE
                assignment_teacher_id := NULL;
            END IF;

            IF assignment_teacher_id IS NULL
               OR assignment_teacher_id = ''
               OR assignment_teacher_type IS DISTINCT FROM NEW.teacher_id_type
               OR assignment_teacher_id IS DISTINCT FROM NEW.teacher_id THEN
                RAISE EXCEPTION
                    'DTS_V2_PARTICIPATION_TEACHER_PROVENANCE_INVALID: typed participation teacher differs from the authoritative appoint image';
            END IF;

            RETURN NULL;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_source_course_history_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            version_position jsonb;
            version_row record;
            completion_image jsonb;
            completion_teacher_id text;
            completion_teacher_type text;
            projection_revision bigint;
            projection_image jsonb;
            projection_field_types jsonb;
            projection_source_timestamp timestamptz;
            projection_teacher_id text;
            projection_teacher_type text;
            expected_initial_completion_snapshot jsonb;
            first_end_revision bigint;
            first_end_source_timestamp timestamptz;
            expected_completion_end_time timestamptz;
            expected_completion_student_token text;
            expected_completion_is_peak boolean;
            expected_completion_lesson_local_date date;
            expected_completion_lesson_local_time time;
            source_week numeric;
            source_week_integer integer;
            resolution_changed boolean;
            completion_identity_changed boolean;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.row_version <= OLD.row_version THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_ROW_VERSION_NOT_MONOTONIC: course row_version must strictly increase';
                END IF;
                IF OLD.last_applied_source_revision IS NOT NULL
                   AND (
                        NEW.last_applied_source_revision IS NULL
                        OR NEW.last_applied_source_revision <
                            OLD.last_applied_source_revision
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_LAST_REVISION_REGRESSION: last-applied revision cannot regress or clear';
                END IF;
                IF OLD.conflict_resolved_against_revision IS NOT NULL
                   AND (
                        NEW.conflict_resolved_against_revision IS NULL
                        OR NEW.conflict_resolved_against_revision <
                            OLD.conflict_resolved_against_revision
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_RESOLVED_REVISION_REGRESSION: resolved revision cannot regress or clear';
                END IF;
                IF OLD.completion_frozen_at IS NOT NULL
                   AND NEW.completion_frozen_at IS DISTINCT FROM
                        OLD.completion_frozen_at
                   AND NOT (
                        OLD.completion_conflict_status = 'PENDING'
                        AND NEW.completion_conflict_status = 'RESOLVED_VOID'
                        AND NEW.completion_frozen_at IS NULL
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_COMPLETION_FROZEN_AT_IMMUTABLE: first-end freeze time cannot change outside an approved VOID';
                END IF;

                resolution_changed :=
                    NEW.conflict_resolved_against_revision IS DISTINCT FROM
                        OLD.conflict_resolved_against_revision
                    OR NEW.conflict_resolved_against_position IS DISTINCT FROM
                        OLD.conflict_resolved_against_position;
                completion_identity_changed := ROW(
                    NEW.completion_participation_seq,
                    NEW.completion_teacher_id,
                    NEW.completion_teacher_id_type
                ) IS DISTINCT FROM ROW(
                    OLD.completion_participation_seq,
                    OLD.completion_teacher_id,
                    OLD.completion_teacher_id_type
                );
            ELSE
                resolution_changed :=
                    NEW.conflict_resolved_against_revision IS NOT NULL
                    OR NEW.conflict_resolved_against_position IS NOT NULL;
                completion_identity_changed :=
                    NEW.completion_participation_seq IS NOT NULL;
            END IF;

            IF NEW.completion_conflict_status LIKE 'RESOLVED_%'
               AND (
                    NEW.conflict_resolved_against_revision IS NULL
                    AND NEW.conflict_resolved_against_position IS NULL
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_RESOLUTION_PROVENANCE_REQUIRED: resolved status requires a typed source boundary';
            END IF;
            IF TG_OP = 'UPDATE'
               AND NEW.completion_conflict_status = 'PENDING' THEN
                IF completion_identity_changed
                   OR NEW.completion_source_revision IS DISTINCT FROM
                        OLD.completion_source_revision
                   OR NEW.completion_source_position IS DISTINCT FROM
                        OLD.completion_source_position THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_PENDING_MUST_INHERIT_RESOLUTION: reopened PENDING must retain the prior correction boundary and completion';
                END IF;
                IF NEW.conflict_resolved_against_revision IS NOT NULL
                   AND (
                        OLD.conflict_resolved_against_revision IS NULL
                        OR NEW.conflict_resolved_against_revision
                            IS DISTINCT FROM
                                OLD.conflict_resolved_against_revision
                        OR NEW.conflict_resolved_against_position
                            IS DISTINCT FROM
                                OLD.conflict_resolved_against_position
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_PENDING_MUST_INHERIT_RESOLUTION: reopened PENDING must retain the prior correction boundary and completion';
                END IF;
            ELSIF NEW.completion_conflict_status = 'RESOLVED_KEEP' THEN
                IF TG_OP <> 'UPDATE'
                   OR completion_identity_changed
                   OR NEW.completion_source_revision IS DISTINCT FROM
                        OLD.completion_source_revision
                   OR NEW.completion_source_position IS DISTINCT FROM
                        OLD.completion_source_position
                   OR (
                        resolution_changed
                        AND OLD.completion_conflict_status IS DISTINCT FROM
                            'PENDING'
                   )
                   OR OLD.completion_participation_seq IS NULL THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_KEEP_REQUIRES_FROZEN_COMPLETION: KEEP may only resolve PENDING without changing completion ownership';
                END IF;
            ELSIF NEW.completion_conflict_status = 'RESOLVED_UPDATE' THEN
                IF TG_OP <> 'UPDATE'
                   OR completion_identity_changed
                   OR OLD.completion_participation_seq IS NULL
                   OR (
                        resolution_changed
                        AND OLD.completion_conflict_status IS DISTINCT FROM
                            'PENDING'
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_UPDATE_REQUIRES_FROZEN_TEACHER: UPDATE may only resolve PENDING for the same typed completion teacher';
                END IF;
            ELSIF NEW.completion_conflict_status = 'RESOLVED_TRANSFER' THEN
                IF TG_OP <> 'UPDATE'
                   OR NEW.completion_participation_seq IS NULL
                   OR (
                        (
                            resolution_changed
                            OR completion_identity_changed
                            OR OLD.completion_conflict_status IS DISTINCT FROM
                                'RESOLVED_TRANSFER'
                        )
                        AND OLD.completion_conflict_status IS DISTINCT FROM
                            'PENDING'
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_TRANSFER_REQUIRES_PENDING: typed completion ownership may change only through a PENDING transfer';
                END IF;
            ELSIF NEW.completion_conflict_status = 'RESOLVED_VOID' THEN
                IF NEW.completion_participation_seq IS NOT NULL
                   OR NEW.completion_teacher_id IS NOT NULL
                   OR NEW.completion_teacher_id_type IS NOT NULL
                   OR TG_OP <> 'UPDATE'
                   OR (
                        resolution_changed
                        AND OLD.completion_conflict_status IS DISTINCT FROM
                            'PENDING'
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_VOID_REQUIRES_PENDING: VOID may only resolve PENDING and must clear typed completion ownership';
                END IF;
            END IF;

            IF NEW.last_applied_event_position IS NOT NULL
               AND NEW.last_applied_source_revision IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_LAST_POSITION_WITHOUT_REVISION: course position has no revision';
            END IF;
            IF NEW.last_applied_source_revision IS NOT NULL
               AND NEW.last_applied_event_position IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_LAST_REVISION_WITHOUT_POSITION: course revision has no position';
            END IF;
            IF NEW.last_applied_source_revision IS NOT NULL THEN
                SELECT source_position
                INTO version_position
                FROM public.dts_source_row_versions
                WHERE source_region = NEW.source_region
                  AND source_table = format('%s_appoint', NEW.source_region)
                  AND source_key = NEW.source_appoint_id
                  AND source_row_revision = NEW.last_applied_source_revision;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_LAST_REVISION_INVALID: course last-applied revision is absent from its appoint history';
                END IF;
                IF version_position IS DISTINCT FROM
                        NEW.last_applied_event_position THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_LAST_POSITION_INVALID: course last-applied position differs from its appoint version';
                END IF;
            END IF;

            IF NEW.completion_source_position IS NOT NULL
               AND NEW.completion_source_revision IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_COMPLETION_POSITION_WITHOUT_REVISION: completion position has no revision';
            END IF;
            IF NEW.completion_source_revision IS NOT NULL
               AND NEW.completion_source_position IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_COMPLETION_REVISION_WITHOUT_POSITION: completion revision has no position';
            END IF;
            IF NEW.completion_source_revision IS NOT NULL THEN
                SELECT source_position, version_kind, operation, after_row,
                       source_field_types, source_timestamp
                INTO version_row
                FROM public.dts_source_row_versions
                WHERE source_region = NEW.source_region
                  AND source_table = format('%s_appoint', NEW.source_region)
                  AND source_key = NEW.source_appoint_id
                  AND source_row_revision = NEW.completion_source_revision;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_COMPLETION_REVISION_INVALID: completion revision is absent from its appoint history';
                END IF;
                IF version_row.source_position IS DISTINCT FROM
                        NEW.completion_source_position THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_COMPLETION_POSITION_INVALID: completion position differs from its appoint version';
                END IF;

                IF NEW.conflict_resolved_against_revision IS NOT NULL THEN
                    -- A correction revision is the audited decision boundary,
                    -- not necessarily the version that originally assigned
                    -- the selected teacher.  This provenance remains relevant
                    -- after a later source event reopens the Case as PENDING or
                    -- a subsequent KEEP closes it at a newer revision.
                    IF NEW.conflict_resolved_against_position IS NULL
                       OR NEW.conflict_resolved_against_revision <
                            NEW.completion_source_revision
                    THEN
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_CORRECTION_BOUNDARY_INVALID: corrected completion must use its resolved source boundary';
                    END IF;
                    IF NEW.completion_conflict_status IN (
                        'RESOLVED_UPDATE', 'RESOLVED_TRANSFER'
                    ) AND (
                        NEW.conflict_resolved_against_revision IS DISTINCT FROM
                            NEW.completion_source_revision
                        OR NEW.conflict_resolved_against_position IS DISTINCT FROM
                            NEW.completion_source_position
                    ) THEN
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_CORRECTION_BOUNDARY_INVALID: transfer/update must establish completion at the exact resolved boundary';
                    END IF;
                ELSE
                    completion_image := version_row.after_row;
                    IF completion_image IS NULL
                       OR NOT (
                            (version_row.version_kind = 'CDC'
                             AND version_row.operation IN ('INSERT', 'UPDATE'))
                            OR (version_row.version_kind = 'SNAPSHOT_DIFF'
                                AND version_row.operation IN (
                                    'SNAPSHOT_INSERT', 'SNAPSHOT_UPDATE',
                                    'SNAPSHOT_BOOTSTRAP_PRESENT'
                                ))
                       )
                       OR version_row.source_field_types ->> 'status'
                            IS DISTINCT FROM 'TEXT'
                       OR jsonb_typeof(completion_image -> 'status')
                            IS DISTINCT FROM 'string'
                       OR completion_image ->> 'status' IS DISTINCT FROM 'end'
                    THEN
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_SOURCE_STATUS_INVALID: initial frozen completion source must have an authoritative status end after image';
                    END IF;

                    completion_teacher_type :=
                        version_row.source_field_types ->> 't_id';
                    IF completion_teacher_type = 'TEXT'
                       AND jsonb_typeof(completion_image -> 't_id') = 'string'
                    THEN
                        completion_teacher_id := completion_image ->> 't_id';
                    ELSIF completion_teacher_type = 'NUMERIC'
                          AND jsonb_typeof(completion_image -> 't_id') IN (
                              'number', 'string'
                          )
                    THEN
                        BEGIN
                            completion_teacher_id := trim_scale(
                                (completion_image ->> 't_id')::numeric
                            )::text;
                        EXCEPTION WHEN invalid_text_representation
                                       OR numeric_value_out_of_range THEN
                            completion_teacher_id := NULL;
                        END;
                    ELSE
                        completion_teacher_id := NULL;
                    END IF;
                    IF completion_teacher_id IS NULL
                       OR completion_teacher_id = ''
                       OR completion_teacher_type IS DISTINCT FROM
                            NEW.completion_teacher_id_type
                       OR completion_teacher_id IS DISTINCT FROM
                            NEW.completion_teacher_id
                    THEN
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_SOURCE_TEACHER_INVALID: initial typed frozen teacher differs from its authoritative end version';
                    END IF;
                END IF;
            END IF;

            IF NEW.conflict_resolved_against_position IS NOT NULL
               AND NEW.conflict_resolved_against_revision IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_RESOLVED_POSITION_WITHOUT_REVISION: resolved position has no revision';
            END IF;
            IF NEW.conflict_resolved_against_revision IS NOT NULL
               AND NEW.conflict_resolved_against_position IS NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_RESOLVED_REVISION_WITHOUT_POSITION: resolved revision has no position';
            END IF;
            IF NEW.conflict_resolved_against_revision IS NOT NULL THEN
                SELECT source_position
                INTO version_position
                FROM public.dts_source_row_versions
                WHERE source_region = NEW.source_region
                  AND source_table = format('%s_appoint', NEW.source_region)
                  AND source_key = NEW.source_appoint_id
                  AND source_row_revision = NEW.conflict_resolved_against_revision;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_RESOLVED_REVISION_INVALID: resolved revision is absent from its appoint history';
                END IF;
                IF version_position IS DISTINCT FROM
                        NEW.conflict_resolved_against_position THEN
                    RAISE EXCEPTION
                    'DTS_V2_COURSE_RESOLVED_POSITION_INVALID: resolved position differs from its appoint version';
                END IF;
            END IF;

            -- The flattened completion columns are serving facts (the 24-hour
            -- favorite anchor reads completion_end_time), not an independent
            -- caller-supplied cache.  They must be the normalized projection of
            -- the exact source version that owns the current completion.  A
            -- first-end PENDING row with an explicit NULL teacher has no
            -- completion pointer yet, so its last-applied end version is the
            -- temporary authoritative projection.  VOID has no current
            -- completion and therefore must clear every flattened fact.
            projection_revision := NULL;
            IF NEW.completion_participation_seq IS NOT NULL THEN
                projection_revision := NEW.completion_source_revision;
            ELSIF NEW.initial_completion_snapshot IS NOT NULL
                  AND NEW.completion_conflict_status = 'PENDING'
                  AND jsonb_typeof(
                        NEW.initial_completion_snapshot -> 'teacher_id'
                  ) = 'null' THEN
                projection_revision := NEW.last_applied_source_revision;
            END IF;

            projection_image := NULL;
            projection_field_types := NULL;
            projection_source_timestamp := NULL;
            IF projection_revision IS NOT NULL THEN
                SELECT after_row, source_field_types, source_timestamp
                INTO projection_image, projection_field_types,
                     projection_source_timestamp
                FROM public.dts_source_row_versions
                WHERE source_region = NEW.source_region
                  AND source_table = format(
                        '%s_appoint', NEW.source_region
                  )
                  AND source_key = NEW.source_appoint_id
                  AND source_row_revision = projection_revision;
                IF NOT FOUND OR projection_image IS NULL THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_COMPLETION_PROJECTION_SOURCE_INVALID: current completion has no authoritative after image';
                END IF;
            END IF;

            first_end_revision := NULL;
            first_end_source_timestamp := NULL;
            IF (
                    jsonb_typeof(NEW.initial_completion_snapshot) = 'object'
                    AND jsonb_typeof(
                        NEW.initial_completion_snapshot -> 'status'
                    ) = 'string'
                    AND NEW.initial_completion_snapshot ->> 'status' = 'end'
               ) OR NEW.completion_frozen_at IS NOT NULL THEN
                SELECT source_row_revision, source_timestamp
                INTO first_end_revision, first_end_source_timestamp
                FROM public.dts_source_row_versions
                WHERE source_region = NEW.source_region
                  AND source_table = format(
                        '%s_appoint', NEW.source_region
                  )
                  AND source_key = NEW.source_appoint_id
                  AND (
                        (version_kind = 'CDC'
                         AND operation IN ('INSERT', 'UPDATE'))
                        OR (version_kind = 'SNAPSHOT_DIFF'
                            AND operation IN (
                                'SNAPSHOT_INSERT', 'SNAPSHOT_UPDATE',
                                'SNAPSHOT_BOOTSTRAP_PRESENT'
                            ))
                  )
                  AND source_field_types ->> 'status' = 'TEXT'
                  AND jsonb_typeof(after_row -> 'status') = 'string'
                  AND after_row ->> 'status' = 'end'
                ORDER BY source_row_revision
                LIMIT 1;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_SOURCE_MISMATCH: no authoritative first-end source history exists';
                END IF;
            END IF;

            IF NEW.completion_frozen_at IS NOT NULL
               AND (
                    first_end_source_timestamp IS NULL
                    OR NEW.completion_frozen_at IS DISTINCT FROM
                        first_end_source_timestamp
               ) THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_COMPLETION_FROZEN_AT_SOURCE_MISMATCH: freeze time must equal the first authoritative end event time';
            END IF;

            expected_completion_end_time := NULL;
            expected_completion_student_token := NULL;
            expected_completion_is_peak := NULL;
            expected_completion_lesson_local_date := NULL;
            expected_completion_lesson_local_time := NULL;

            IF projection_image IS NOT NULL THEN
                IF projection_image ? 'end_time'
                   AND jsonb_typeof(projection_image -> 'end_time')
                        IS DISTINCT FROM 'null' THEN
                    IF projection_field_types ->> 'end_time'
                           NOT IN ('TEXT', 'TEMPORAL')
                       OR jsonb_typeof(projection_image -> 'end_time')
                            IS DISTINCT FROM 'string' THEN
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_PROJECTION_SOURCE_INVALID: end_time has no temporal type evidence';
                    END IF;
                    -- A source-local timestamp without a proved offset is kept
                    -- only in the raw snapshot; the calculable timestamptz fact
                    -- must stay NULL rather than inheriting the database
                    -- session timezone.
                    IF projection_image ->> 'end_time'
                           ~ '[T ][0-9]{2}:[0-9]{2}.*(Z|[+-][0-9]{2}([:]?[0-9]{2})?)$'
                    THEN
                        BEGIN
                            expected_completion_end_time :=
                                (projection_image ->> 'end_time')::timestamptz;
                        EXCEPTION WHEN invalid_datetime_format
                                       OR datetime_field_overflow THEN
                            expected_completion_end_time := NULL;
                        END;
                    END IF;
                END IF;

                IF projection_image ? 'student_token'
                   AND jsonb_typeof(projection_image -> 'student_token')
                        IS DISTINCT FROM 'null' THEN
                    IF projection_field_types ->> 'student_token'
                           IS DISTINCT FROM 'TEXT'
                       OR jsonb_typeof(projection_image -> 'student_token')
                            IS DISTINCT FROM 'string' THEN
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_PROJECTION_SOURCE_INVALID: student_token has no text type evidence';
                    END IF;
                    expected_completion_student_token :=
                        projection_image ->> 'student_token';
                ELSIF NEW.source_region = 'ovs'
                      AND projection_image ? 's_id'
                      AND jsonb_typeof(projection_image -> 's_id')
                           IS DISTINCT FROM 'null' THEN
                    IF projection_field_types ->> 's_id' = 'TEXT'
                       AND jsonb_typeof(projection_image -> 's_id') = 'string'
                    THEN
                        expected_completion_student_token :=
                            projection_image ->> 's_id';
                    ELSIF projection_field_types ->> 's_id' = 'NUMERIC'
                          AND jsonb_typeof(projection_image -> 's_id') IN (
                                'number', 'string'
                          ) THEN
                        BEGIN
                            expected_completion_student_token := trim_scale(
                                (projection_image ->> 's_id')::numeric
                            )::text;
                        EXCEPTION WHEN invalid_text_representation
                                       OR numeric_value_out_of_range THEN
                            RAISE EXCEPTION
                                'DTS_V2_COURSE_COMPLETION_PROJECTION_SOURCE_INVALID: OVS student identity cannot be normalized';
                        END;
                    ELSE
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_PROJECTION_SOURCE_INVALID: OVS student identity has no typed evidence';
                    END IF;
                END IF;

                IF projection_image ? 'date'
                   AND jsonb_typeof(projection_image -> 'date')
                        IS DISTINCT FROM 'null' THEN
                    IF projection_field_types ->> 'date'
                           NOT IN ('TEXT', 'TEMPORAL')
                       OR jsonb_typeof(projection_image -> 'date')
                            IS DISTINCT FROM 'string' THEN
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_PROJECTION_SOURCE_INVALID: lesson date has no temporal type evidence';
                    END IF;
                    BEGIN
                        expected_completion_lesson_local_date :=
                            (projection_image ->> 'date')::date;
                    EXCEPTION WHEN invalid_datetime_format
                                   OR datetime_field_overflow THEN
                        expected_completion_lesson_local_date := NULL;
                    END;
                END IF;

                IF projection_image ? 'time'
                   AND jsonb_typeof(projection_image -> 'time')
                        IS DISTINCT FROM 'null' THEN
                    IF projection_field_types ->> 'time'
                           NOT IN ('TEXT', 'TEMPORAL')
                       OR jsonb_typeof(projection_image -> 'time')
                            IS DISTINCT FROM 'string' THEN
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_PROJECTION_SOURCE_INVALID: lesson time has no temporal type evidence';
                    END IF;
                    BEGIN
                        expected_completion_lesson_local_time :=
                            (projection_image ->> 'time')::time;
                    EXCEPTION WHEN invalid_datetime_format
                                   OR datetime_field_overflow THEN
                        expected_completion_lesson_local_time := NULL;
                    END;
                END IF;

                IF projection_image ? 'week'
                   AND jsonb_typeof(projection_image -> 'week')
                        IS DISTINCT FROM 'null'
                   AND expected_completion_lesson_local_time IS NOT NULL THEN
                    IF projection_field_types ->> 'week'
                           IS DISTINCT FROM 'NUMERIC'
                       OR jsonb_typeof(projection_image -> 'week')
                            NOT IN ('number', 'string') THEN
                        RAISE EXCEPTION
                            'DTS_V2_COURSE_COMPLETION_PROJECTION_SOURCE_INVALID: week has no numeric type evidence';
                    END IF;
                    BEGIN
                        source_week :=
                            (projection_image ->> 'week')::numeric;
                    EXCEPTION WHEN invalid_text_representation
                                   OR numeric_value_out_of_range THEN
                        source_week := NULL;
                    END;
                    IF source_week = trunc(source_week)
                       AND source_week IN (0, 1, 2, 3, 4, 5, 6, 7) THEN
                        source_week_integer := source_week::integer;
                        IF NEW.source_region = 'dom' THEN
                            expected_completion_is_peak :=
                                expected_completion_lesson_local_time
                                    BETWEEN time '18:00' AND time '21:30'
                                OR (
                                    source_week_integer IN (0, 6, 7)
                                    AND expected_completion_lesson_local_time
                                        BETWEEN time '09:00' AND time '11:30'
                                );
                        ELSE
                            expected_completion_is_peak :=
                                expected_completion_lesson_local_time
                                    BETWEEN time '18:00' AND time '23:30'
                                OR expected_completion_lesson_local_time
                                    BETWEEN time '00:00' AND time '05:30'
                                OR (
                                    source_week_integer IN (0, 6, 7)
                                    AND expected_completion_lesson_local_time
                                        BETWEEN time '09:00' AND time '11:30'
                                );
                        END IF;
                    END IF;
                END IF;
            END IF;

            -- The immutable initial snapshot is the complete, normalized
            -- first-end image, not a caller-selected subset.  Bind every
            -- ordinary first-end field to the exact persisted source version
            -- at the moment the snapshot is established.  Later correction
            -- decisions may move completion ownership but cannot rewrite this
            -- historical image.
            IF NEW.completion_participation_seq IS NOT NULL
               AND NEW.initial_completion_snapshot IS NOT NULL
               AND (
                    TG_OP = 'INSERT'
                    OR OLD.initial_completion_snapshot IS NULL
               ) THEN
                IF NEW.completion_source_revision IS DISTINCT FROM
                        first_end_revision
                   OR projection_image IS NULL
                   OR projection_field_types ->> 'status'
                        IS DISTINCT FROM 'TEXT'
                   OR jsonb_typeof(projection_image -> 'status')
                        IS DISTINCT FROM 'string'
                   OR projection_image ->> 'status' IS DISTINCT FROM 'end'
                THEN
                    RAISE EXCEPTION
                        'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_SOURCE_MISMATCH: first-end snapshot has no authoritative end image';
                END IF;

                projection_teacher_type :=
                    projection_field_types ->> 't_id';
                IF NOT (projection_image ? 't_id')
                   OR jsonb_typeof(projection_image -> 't_id') = 'null' THEN
                    projection_teacher_id := NULL;
                    projection_teacher_type := NULL;
                ELSIF projection_teacher_type = 'TEXT'
                      AND jsonb_typeof(projection_image -> 't_id') = 'string'
                THEN
                    projection_teacher_id := projection_image ->> 't_id';
                ELSIF projection_teacher_type = 'NUMERIC'
                      AND jsonb_typeof(projection_image -> 't_id') IN (
                          'number', 'string'
                      ) THEN
                    BEGIN
                        projection_teacher_id := trim_scale(
                            (projection_image ->> 't_id')::numeric
                        )::text;
                    EXCEPTION WHEN invalid_text_representation
                                   OR numeric_value_out_of_range THEN
                        projection_teacher_id := NULL;
                    END;
                ELSE
                    projection_teacher_id := NULL;
                END IF;
                IF projection_teacher_id IS NULL
                   OR projection_teacher_id = ''
                   OR projection_teacher_type IS DISTINCT FROM
                        NEW.completion_teacher_id_type
                   OR projection_teacher_id IS DISTINCT FROM
                        NEW.completion_teacher_id THEN
                    RAISE EXCEPTION
                        'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_SOURCE_MISMATCH: first-end snapshot teacher differs from its authoritative image';
                END IF;

                IF projection_image ? 'use_point'
                   AND jsonb_typeof(projection_image -> 'use_point')
                        IS DISTINCT FROM 'null'
                   AND (
                        projection_field_types ->> 'use_point'
                            IS DISTINCT FROM 'TEXT'
                        OR jsonb_typeof(projection_image -> 'use_point')
                            IS DISTINCT FROM 'string'
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_SOURCE_MISMATCH: use_point has no text type evidence';
                END IF;

                expected_initial_completion_snapshot := jsonb_build_object(
                    'teacher_id', projection_teacher_id,
                    'teacher_id_type', projection_teacher_type,
                    'status', projection_image ->> 'status',
                    'use_point', CASE
                        WHEN jsonb_typeof(projection_image -> 'use_point') =
                            'string'
                        THEN projection_image ->> 'use_point'
                        ELSE NULL
                    END,
                    'end_time', CASE
                        WHEN jsonb_typeof(projection_image -> 'end_time') =
                            'string'
                        THEN projection_image ->> 'end_time'
                        ELSE NULL
                    END,
                    'student_token', expected_completion_student_token,
                    'lesson_local_date', CASE
                        WHEN jsonb_typeof(projection_image -> 'date') =
                            'string'
                        THEN projection_image ->> 'date'
                        ELSE NULL
                    END,
                    'lesson_local_time', CASE
                        WHEN jsonb_typeof(projection_image -> 'time') =
                            'string'
                        THEN projection_image ->> 'time'
                        ELSE NULL
                    END,
                    'is_peak', expected_completion_is_peak
                );
                IF jsonb_typeof(
                        NEW.initial_completion_snapshot -> 'teacher_id'
                   ) = 'string'
                   AND jsonb_typeof(
                        NEW.initial_completion_snapshot -> 'teacher_id_type'
                   ) = 'string'
                   AND ROW(
                        NEW.initial_completion_snapshot ->> 'teacher_id_type',
                        NEW.initial_completion_snapshot ->> 'teacher_id'
                   ) IS DISTINCT FROM ROW(
                        NEW.completion_teacher_id_type,
                        NEW.completion_teacher_id
                   ) THEN
                    RAISE EXCEPTION
                        'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_TEACHER_MISMATCH: typed snapshot teacher differs from the initial completion pointer';
                END IF;
                IF NEW.initial_completion_snapshot IS DISTINCT FROM
                        expected_initial_completion_snapshot THEN
                    RAISE EXCEPTION
                        'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_SOURCE_MISMATCH: immutable first-end snapshot differs from its authoritative image';
                END IF;
                IF projection_source_timestamp IS NULL
                   OR NEW.completion_frozen_at IS DISTINCT FROM
                        projection_source_timestamp THEN
                    RAISE EXCEPTION
                        'DTS_V2_COURSE_COMPLETION_FROZEN_AT_SOURCE_MISMATCH: freeze time must equal the authoritative first-end event time';
                END IF;
            END IF;

            IF NEW.completion_end_time IS DISTINCT FROM
                    expected_completion_end_time
               OR NEW.completion_student_token IS DISTINCT FROM
                    expected_completion_student_token
               OR NEW.completion_is_peak IS DISTINCT FROM
                    expected_completion_is_peak
               OR NEW.completion_lesson_local_date IS DISTINCT FROM
                    expected_completion_lesson_local_date
               OR NEW.completion_lesson_local_time IS DISTINCT FROM
                    expected_completion_lesson_local_time THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_COMPLETION_DERIVED_FACT_MISMATCH: flattened completion facts differ from the authoritative completion source version';
            END IF;

            -- Validate the referenced boundary before interpreting its
            -- correction state.  This preserves the more specific
            -- revision/position diagnostics and, more importantly, prevents
            -- a syntactically complete but forged boundary from being
            -- treated as correction provenance.
            IF NEW.completion_conflict_status = 'NONE'
               AND NEW.conflict_resolved_against_revision IS NOT NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_RESOLUTION_STATUS_INVALID: NONE cannot carry correction provenance';
            END IF;
            IF TG_OP = 'INSERT'
               AND NEW.conflict_resolved_against_revision IS NOT NULL THEN
                RAISE EXCEPTION
                    'DTS_V2_COURSE_FIRST_RESOLUTION_REQUIRES_HISTORY: correction provenance cannot be preloaded';
            END IF;

            RETURN NULL;
        END
        $function$;

        CREATE FUNCTION public.dts_v2_initial_completion_snapshot_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            snapshot_teacher_id text;
            snapshot_teacher_type text;
            snapshot_version record;
            establishing_transfer boolean := false;
            inherited_transfer boolean := false;
            snapshot_without_pointer_allowed boolean := false;
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.initial_completion_snapshot IS NOT NULL
               AND NEW.initial_completion_snapshot IS DISTINCT FROM
                    OLD.initial_completion_snapshot THEN
                RAISE EXCEPTION
                    'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_IMMUTABLE: initial completion snapshot cannot change or clear';
            END IF;

            IF NEW.initial_completion_snapshot IS NOT NULL THEN
                IF jsonb_typeof(NEW.initial_completion_snapshot)
                       IS DISTINCT FROM 'object'
                   OR jsonb_typeof(
                       NEW.initial_completion_snapshot -> 'status'
                   ) IS DISTINCT FROM 'string'
                   OR NEW.initial_completion_snapshot ->> 'status'
                       IS DISTINCT FROM 'end' THEN
                    RAISE EXCEPTION
                        'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_INVALID: initial completion snapshot must be an object with status end';
                END IF;

                IF jsonb_typeof(
                    NEW.initial_completion_snapshot -> 'teacher_id'
                ) = 'null' THEN
                    IF NEW.initial_completion_snapshot ? 'teacher_id_type'
                       AND jsonb_typeof(
                            NEW.initial_completion_snapshot -> 'teacher_id_type'
                       ) IS DISTINCT FROM 'null' THEN
                        RAISE EXCEPTION
                            'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_TEACHER_TYPE_INVALID: NULL teacher must not carry a source type';
                    END IF;
                    snapshot_teacher_id := NULL;
                    snapshot_teacher_type := NULL;
                ELSIF jsonb_typeof(
                    NEW.initial_completion_snapshot -> 'teacher_id'
                ) = 'string'
                   AND jsonb_typeof(
                    NEW.initial_completion_snapshot -> 'teacher_id_type'
                ) = 'string'
                   AND NEW.initial_completion_snapshot ->> 'teacher_id_type'
                        IN ('NUMERIC', 'TEXT') THEN
                    snapshot_teacher_id :=
                        NEW.initial_completion_snapshot ->> 'teacher_id';
                    snapshot_teacher_type :=
                        NEW.initial_completion_snapshot ->> 'teacher_id_type';
                ELSE
                    RAISE EXCEPTION
                        'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_TEACHER_TYPE_INVALID: snapshot teacher requires NUMERIC/TEXT type evidence';
                END IF;

                IF snapshot_teacher_id = '' THEN
                    RAISE EXCEPTION
                        'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_TEACHER_TYPE_INVALID: snapshot teacher must not be empty';
                END IF;
                IF snapshot_teacher_type = 'NUMERIC' THEN
                    BEGIN
                        IF trim_scale(snapshot_teacher_id::numeric)::text
                           IS DISTINCT FROM snapshot_teacher_id THEN
                            RAISE EXCEPTION
                                'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_TEACHER_TYPE_INVALID: numeric snapshot teacher must be canonical';
                        END IF;
                    EXCEPTION WHEN invalid_text_representation
                                   OR numeric_value_out_of_range THEN
                        RAISE EXCEPTION
                            'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_TEACHER_TYPE_INVALID: numeric snapshot teacher is invalid';
                    END;
                END IF;

                IF NEW.completion_participation_seq IS NOT NULL THEN
                    IF ROW(snapshot_teacher_type, snapshot_teacher_id)
                       IS DISTINCT FROM ROW(
                            NEW.completion_teacher_id_type,
                            NEW.completion_teacher_id
                       ) THEN
                        IF TG_OP = 'UPDATE' THEN
                            establishing_transfer :=
                                NEW.completion_conflict_status =
                                    'RESOLVED_TRANSFER'
                                AND OLD.completion_conflict_status = 'PENDING'
                                AND NEW.conflict_resolved_against_revision
                                    IS NOT NULL
                                AND NEW.conflict_resolved_against_revision
                                    IS NOT DISTINCT FROM
                                        NEW.completion_source_revision
                                AND NEW.conflict_resolved_against_position
                                    IS NOT DISTINCT FROM
                                        NEW.completion_source_position;
                            inherited_transfer :=
                                OLD.completion_participation_seq IS NOT NULL
                                AND OLD.conflict_resolved_against_revision
                                    IS NOT NULL
                                AND ROW(
                                    NEW.completion_participation_seq,
                                    NEW.completion_teacher_id_type,
                                    NEW.completion_teacher_id
                                ) IS NOT DISTINCT FROM ROW(
                                    OLD.completion_participation_seq,
                                    OLD.completion_teacher_id_type,
                                    OLD.completion_teacher_id
                                )
                                AND ROW(
                                    snapshot_teacher_type,
                                    snapshot_teacher_id
                                ) IS DISTINCT FROM ROW(
                                    OLD.completion_teacher_id_type,
                                    OLD.completion_teacher_id
                                );
                        END IF;
                        IF NOT establishing_transfer
                           AND NOT inherited_transfer THEN
                            RAISE EXCEPTION
                                'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_TEACHER_MISMATCH: typed snapshot teacher may differ only after an exact approved transfer';
                        END IF;
                    END IF;
                ELSE
                    snapshot_without_pointer_allowed :=
                        (
                            snapshot_teacher_id IS NULL
                            AND NEW.completion_conflict_status = 'PENDING'
                        )
                        OR NEW.completion_conflict_status = 'RESOLVED_VOID'
                        OR (
                            TG_OP = 'UPDATE'
                            AND NEW.completion_conflict_status = 'PENDING'
                            AND OLD.completion_participation_seq IS NULL
                            AND OLD.conflict_resolved_against_revision IS NOT NULL
                            AND NEW.conflict_resolved_against_revision
                                IS NOT DISTINCT FROM
                                    OLD.conflict_resolved_against_revision
                        );
                    IF NOT snapshot_without_pointer_allowed THEN
                        RAISE EXCEPTION
                            'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_POINTER_INVALID: snapshot without completion is allowed only for missing-teacher PENDING or approved VOID';
                    END IF;

                    -- The special first-end PENDING shape is legal only when
                    -- its appoint version actually proves status=end with a
                    -- missing t_id.  Otherwise a caller could suppress a real
                    -- completion owner by fabricating a NULL-teacher snapshot
                    -- and later presenting the source row as a correction.
                    IF snapshot_teacher_id IS NULL
                       AND NEW.completion_conflict_status = 'PENDING'
                       AND (
                            TG_OP = 'INSERT'
                            OR OLD.initial_completion_snapshot IS NULL
                       ) THEN
                        IF NEW.last_applied_source_revision IS NULL
                           OR NEW.last_applied_event_position IS NULL THEN
                            RAISE EXCEPTION
                                'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_MISSING_TEACHER_SOURCE_INVALID: missing-teacher first end requires its authoritative appoint revision';
                        END IF;

                        SELECT source_position, version_kind, operation,
                               after_row, source_field_types
                        INTO snapshot_version
                        FROM public.dts_source_row_versions
                        WHERE source_region = NEW.source_region
                          AND source_table = format(
                              '%s_appoint', NEW.source_region
                          )
                          AND source_key = NEW.source_appoint_id
                          AND source_row_revision =
                              NEW.last_applied_source_revision;

                        IF NOT FOUND
                           OR snapshot_version.source_position IS DISTINCT FROM
                                NEW.last_applied_event_position
                           OR snapshot_version.after_row IS NULL
                           OR NOT (
                                (
                                    snapshot_version.version_kind = 'CDC'
                                    AND snapshot_version.operation IN (
                                        'INSERT', 'UPDATE'
                                    )
                                )
                                OR (
                                    snapshot_version.version_kind =
                                        'SNAPSHOT_DIFF'
                                    AND snapshot_version.operation IN (
                                        'SNAPSHOT_INSERT',
                                        'SNAPSHOT_UPDATE',
                                        'SNAPSHOT_BOOTSTRAP_PRESENT'
                                    )
                                )
                           )
                           OR snapshot_version.source_field_types ->> 'status'
                                IS DISTINCT FROM 'TEXT'
                           OR jsonb_typeof(
                                snapshot_version.after_row -> 'status'
                           ) IS DISTINCT FROM 'string'
                           OR snapshot_version.after_row ->> 'status'
                                IS DISTINCT FROM 'end'
                           OR snapshot_version.source_field_types ->> 't_id'
                                IS NULL
                           OR snapshot_version.source_field_types ->> 't_id'
                                NOT IN ('NUMERIC', 'TEXT')
                           OR (
                                snapshot_version.after_row ? 't_id'
                                AND jsonb_typeof(
                                    snapshot_version.after_row -> 't_id'
                                ) IS DISTINCT FROM 'null'
                           ) THEN
                            RAISE EXCEPTION
                                'DTS_V2_INITIAL_COMPLETION_SNAPSHOT_MISSING_TEACHER_SOURCE_INVALID: missing-teacher first end differs from its authoritative appoint version';
                        END IF;
                    END IF;
                END IF;
            END IF;
            RETURN NULL;
        END
        $function$;

        CREATE CONSTRAINT TRIGGER ct_source_course_bidirectional_guard
        AFTER INSERT OR UPDATE OR DELETE ON public.source_courses
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_course_participation_bidirectional_guard();

        CREATE CONSTRAINT TRIGGER ct_source_course_participation_bidirectional_guard
        AFTER INSERT OR UPDATE OR DELETE ON public.source_course_participations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_course_participation_bidirectional_guard();

        CREATE CONSTRAINT TRIGGER ct_source_course_participation_provenance_guard
        AFTER INSERT OR UPDATE ON public.source_course_participations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_course_participation_provenance_guard();

        CREATE CONSTRAINT TRIGGER ct_source_course_history_guard
        AFTER INSERT OR UPDATE ON public.source_courses
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_source_course_history_guard();

        CREATE CONSTRAINT TRIGGER ct_source_course_initial_completion_snapshot_guard
        AFTER INSERT OR UPDATE ON public.source_courses
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.dts_v2_initial_completion_snapshot_guard();
        """
    )


def _lock_shadow_guards_from_runtime() -> None:
    op.execute(
        """
        REVOKE ALL PRIVILEGES ON TABLE
            public.source_courses,
            public.source_course_participations
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        REVOKE ALL PRIVILEGES ON FUNCTION
            public.dts_v2_course_participation_bidirectional_guard(),
            public.dts_v2_course_participation_provenance_guard(),
            public.dts_v2_source_course_history_guard(),
            public.dts_v2_initial_completion_snapshot_guard()
        FROM PUBLIC, tit_growth_app, tit_dts_ingest_runtime;

        DO $shadow_guard_acl$
        BEGIN
            IF to_regrole('tit_teacher_crud') IS NOT NULL THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
                    'public.source_courses, '
                    'public.source_course_participations FROM tit_teacher_crud';
                EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION '
                    'public.dts_v2_course_participation_bidirectional_guard(), '
                    'public.dts_v2_course_participation_provenance_guard(), '
                    'public.dts_v2_source_course_history_guard(), '
                    'public.dts_v2_initial_completion_snapshot_guard() '
                    'FROM tit_teacher_crud';
            END IF;
        END
        $shadow_guard_acl$;
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _create_deferred_guards()
    _lock_shadow_guards_from_runtime()
    op.execute(
        """
        COMMENT ON TABLE public.source_courses IS
            'DTS v2 shadow only; rev68 enforces frozen initial snapshot, source history, and bidirectional participation pointers; no runtime writer or score-ownership route is active';
        COMMENT ON TABLE public.source_course_participations IS
            'DTS v2 shadow only; rev68 enforces appoint provenance, completion status, and reverse pointers; no runtime writer or correction command is active';
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        LOCK TABLE
            public.source_course_participations,
            public.source_courses
        IN ACCESS EXCLUSIVE MODE;

        DO $course_participation_guard_downgrade_guard$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.source_courses LIMIT 1)
               OR EXISTS (
                    SELECT 1 FROM public.source_course_participations LIMIT 1
               )
            THEN
                RAISE EXCEPTION
                    'refusing course-participation guard downgrade: shadow data exists';
            END IF;
        END
        $course_participation_guard_downgrade_guard$;
        """
    )
    op.execute(
        """
        DROP TRIGGER ct_source_course_initial_completion_snapshot_guard
            ON public.source_courses;
        DROP TRIGGER ct_source_course_history_guard ON public.source_courses;
        DROP TRIGGER ct_source_course_participation_provenance_guard
            ON public.source_course_participations;
        DROP TRIGGER ct_source_course_participation_bidirectional_guard
            ON public.source_course_participations;
        DROP TRIGGER ct_source_course_bidirectional_guard ON public.source_courses;

        DROP FUNCTION public.dts_v2_initial_completion_snapshot_guard();
        DROP FUNCTION public.dts_v2_source_course_history_guard();
        DROP FUNCTION public.dts_v2_course_participation_provenance_guard();
        DROP FUNCTION public.dts_v2_course_participation_bidirectional_guard();

        COMMENT ON TABLE public.source_courses IS
            'DTS v2 shadow only; frozen-field and reverse-pointer guards not activated';
        COMMENT ON TABLE public.source_course_participations IS
            'DTS v2 shadow only; provenance semantic guard not yet activated';
        """
    )
