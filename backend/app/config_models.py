from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConfigKey(str, Enum):
    SCORE_GRADUATION = "SCORE_GRADUATION"
    AGENT_POLICY = "AGENT_POLICY"
    DELIVERY_POLICY = "DELIVERY_POLICY"


class ConfigStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


HIGH_IMPACT_CONFIG_KEYS = frozenset(ConfigKey)


class DimensionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cap: float = Field(gt=0)
    weight: float = Field(gt=0, le=1)
    minimum_score: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_minimum_not_above_cap(self) -> "DimensionRule":
        if self.minimum_score > self.cap:
            raise ValueError("维度最低线不能高于该维度分数上限")
        return self


class FixedBaseScoreRule(BaseModel):
    """A base-score input that is already expressed in points."""

    model_config = ConfigDict(extra="forbid")

    maximum_points: float = Field(gt=0)


class CapacityMilestoneScoreRule(BaseModel):
    """The only v4 capacity award: a locked teacher-wide Peak-slot milestone."""

    model_config = ConfigDict(extra="forbid")

    milestone_id: Literal["CAPACITY_PEAK_SLOT_40"] = "CAPACITY_PEAK_SLOT_40"
    metric: Literal["peak_slot_cnt"] = "peak_slot_cnt"
    operator: Literal["GTE"] = "GTE"
    threshold: int = Field(gt=0)
    score_value: float = Field(gt=0)
    maximum_points: float = Field(gt=0)
    settlement_mode: Literal["FIRST_ACHIEVEMENT_LOCKED"] = "FIRST_ACHIEVEMENT_LOCKED"

    @model_validator(mode="after")
    def validate_award_not_above_maximum(self) -> "CapacityMilestoneScoreRule":
        if self.score_value > self.maximum_points:
            raise ValueError("供给里程碑得分不能高于供给维度上限")
        return self


class UnitScoreRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points_per_unit: float = Field(gt=0)


class ClassroomQualityScoreRule(UnitScoreRule):
    default_achievement_rate: float = Field(ge=0, le=1)
    source_mode: Literal["MOCK_SIMULATION"] = "MOCK_SIMULATION"


class PerfectCompletionQualityScoreRule(UnitScoreRule):
    """Current classroom-quality rule backed by the teacher-wide perfect count."""

    model_config = ConfigDict(extra="forbid")

    metric: Literal["perfect_cnt"] = "perfect_cnt"
    source_mode: Literal["REAL_TEACHER_SNAPSHOT"] = "REAL_TEACHER_SNAPSHOT"


class ScoreItemsV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capacity: Union[FixedBaseScoreRule, CapacityMilestoneScoreRule]
    new_teacher_tasks: FixedBaseScoreRule
    feedback_praise: UnitScoreRule
    feedback_favorite: UnitScoreRule
    feedback_rebook_15d: UnitScoreRule
    reliability_on_time: UnitScoreRule
    reliability_peak: UnitScoreRule
    classroom_quality: Union[
        ClassroomQualityScoreRule,
        PerfectCompletionQualityScoreRule,
    ]


class ScoreThresholdsV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graduation_raw_score: float = Field(gt=0)
    gold_raw_score: float = Field(gt=0)
    graduation_external_score: float = Field(gt=0, le=200)
    gold_external_score: float = Field(gt=0, le=200)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "ScoreThresholdsV2":
        if self.gold_raw_score <= self.graduation_raw_score:
            raise ValueError("金牌原始分阈值必须高于出营原始分阈值")
        if self.gold_external_score <= self.graduation_external_score:
            raise ValueError("金牌对外分必须高于出营对外分")
        return self


class GraduationHardGatesV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_base_score: float = Field(ge=0)
    minimum_completed_lessons: int = Field(ge=0)
    minimum_user_feedback_score_exclusive: float = Field(ge=0)
    minimum_reliability_score_exclusive: float = Field(ge=0)
    allow_severe_redline: bool = False


class GoldHardGatesV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_base_score: float = Field(ge=0)
    minimum_completed_lessons: int = Field(ge=0)
    minimum_user_feedback_score: float = Field(ge=0)
    maximum_late_count: int = Field(ge=0)
    maximum_early_count: int = Field(ge=0)
    maximum_real_absent_count: int = Field(ge=0)


class ScoreHardGatesV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graduation: GraduationHardGatesV2
    gold: GoldHardGatesV2


class GraduationHardGatesV5(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_mandatory_task_count: int = Field(gt=0)
    maximum_l0_complaint_count: int = Field(ge=0)


class ScoreHardGatesV5(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graduation: GraduationHardGatesV5
    gold: GoldHardGatesV2


class GoldHardGatesV6(BaseModel):
    """Current Gold contract: graduation is inherited; the score line lives in thresholds."""

    model_config = ConfigDict(extra="forbid")

    inherits_graduation: Literal[True] = True


class ScoreHardGatesV6(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graduation: GraduationHardGatesV5
    gold: GoldHardGatesV6


class ScoreGraduationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # v2-v5 remain readable historical contracts. New environments use v6,
    # which keeps v5 scoring/graduation and removes the retired Gold-only gates.
    policy_version: Literal["v2", "v3", "v4", "v5", "v6"]
    scoring_items: ScoreItemsV2
    thresholds: ScoreThresholdsV2
    hard_gates: Union[ScoreHardGatesV2, ScoreHardGatesV5, ScoreHardGatesV6]
    graduation_effect: Literal["IMMEDIATE_ON_CRITERIA"] = "IMMEDIATE_ON_CRITERIA"

    @model_validator(mode="after")
    def validate_cross_field_contract(self) -> "ScoreGraduationConfig":
        base_maximum = (
            self.scoring_items.capacity.maximum_points
            + self.scoring_items.new_teacher_tasks.maximum_points
        )
        graduation_rule = self.hard_gates.graduation
        graduation_base_requirement = (
            self.scoring_items.new_teacher_tasks.maximum_points
            if isinstance(graduation_rule, GraduationHardGatesV5)
            else graduation_rule.minimum_base_score
        )
        if graduation_base_requirement > base_maximum:
            raise ValueError("出营基础分硬门槛不能高于基础分满分")
        if (
            isinstance(self.hard_gates.gold, GoldHardGatesV2)
            and self.hard_gates.gold.required_base_score > base_maximum
        ):
            raise ValueError("金牌基础分硬门槛不能高于基础分满分")
        if self.policy_version == "v3":
            # v3 is the locked 2026-07-20 meeting decision, not a family of
            # configurable policies.  Any semantic change must receive a new
            # policy version instead of silently reusing the v3 label.
            expected_scoring_items = {
                "capacity": {"maximum_points": 10.0},
                "new_teacher_tasks": {"maximum_points": 30.0},
                "feedback_praise": {"points_per_unit": 5.0},
                "feedback_favorite": {"points_per_unit": 5.0},
                "feedback_rebook_15d": {"points_per_unit": 8.0},
                "reliability_on_time": {"points_per_unit": 2.0},
                "reliability_peak": {"points_per_unit": 1.0},
                "classroom_quality": {
                    "points_per_unit": 2.0,
                    "default_achievement_rate": 0.8,
                    "source_mode": "MOCK_SIMULATION",
                },
            }
            expected_thresholds = {
                "graduation_raw_score": 100.0,
                "gold_raw_score": 200.0,
                "graduation_external_score": 100.0,
                "gold_external_score": 200.0,
            }
            expected_hard_gates = {
                "graduation": {
                    "minimum_base_score": 30.0,
                    "minimum_completed_lessons": 10,
                    "minimum_user_feedback_score_exclusive": 0.0,
                    "minimum_reliability_score_exclusive": 0.0,
                    "allow_severe_redline": False,
                },
                "gold": {
                    "required_base_score": 40.0,
                    "minimum_completed_lessons": 10,
                    "minimum_user_feedback_score": 20.0,
                    "maximum_late_count": 1,
                    "maximum_early_count": 0,
                    "maximum_real_absent_count": 0,
                },
            }
            if (
                self.scoring_items.model_dump(mode="json") != expected_scoring_items
                or self.thresholds.model_dump(mode="json") != expected_thresholds
                or self.hard_gates.model_dump(mode="json") != expected_hard_gates
            ):
                raise ValueError(
                    "v3 已冻结 2026-07-20 的计分项、任务上限、出营/金牌分数线和"
                    "全部资格硬门槛；业务语义变化必须发布新的 policy_version"
                )
        if self.policy_version == "v4":
            expected_scoring_items = {
                "capacity": {
                    "milestone_id": "CAPACITY_PEAK_SLOT_40",
                    "metric": "peak_slot_cnt",
                    "operator": "GTE",
                    "threshold": 40,
                    "score_value": 10.0,
                    "maximum_points": 10.0,
                    "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
                },
                "new_teacher_tasks": {"maximum_points": 30.0},
                "feedback_praise": {"points_per_unit": 5.0},
                "feedback_favorite": {"points_per_unit": 5.0},
                "feedback_rebook_15d": {"points_per_unit": 8.0},
                "reliability_on_time": {"points_per_unit": 2.0},
                "reliability_peak": {"points_per_unit": 1.0},
                "classroom_quality": {
                    "points_per_unit": 2.0,
                    "default_achievement_rate": 0.8,
                    "source_mode": "MOCK_SIMULATION",
                },
            }
            expected_thresholds = {
                "graduation_raw_score": 100.0,
                "gold_raw_score": 200.0,
                "graduation_external_score": 100.0,
                "gold_external_score": 200.0,
            }
            expected_hard_gates = {
                "graduation": {
                    "minimum_base_score": 30.0,
                    "minimum_completed_lessons": 10,
                    "minimum_user_feedback_score_exclusive": 0.0,
                    "minimum_reliability_score_exclusive": 0.0,
                    "allow_severe_redline": False,
                },
                "gold": {
                    "required_base_score": 40.0,
                    "minimum_completed_lessons": 10,
                    "minimum_user_feedback_score": 20.0,
                    "maximum_late_count": 1,
                    "maximum_early_count": 0,
                    "maximum_real_absent_count": 0,
                },
            }
            if (
                self.scoring_items.model_dump(mode="json") != expected_scoring_items
                or self.thresholds.model_dump(mode="json") != expected_thresholds
                or self.hard_gates.model_dump(mode="json") != expected_hard_gates
            ):
                raise ValueError(
                    "v4 已冻结 CAPACITY_PEAK_SLOT_40（peak_slot_cnt >= 40 加 10 分）及其余"
                    "2026-07-20 计分和资格门槛；再次变化必须发布新的 policy_version"
                )
        if self.policy_version == "v5":
            expected_scoring_items = {
                "capacity": {
                    "milestone_id": "CAPACITY_PEAK_SLOT_40",
                    "metric": "peak_slot_cnt",
                    "operator": "GTE",
                    "threshold": 40,
                    "score_value": 10.0,
                    "maximum_points": 10.0,
                    "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
                },
                "new_teacher_tasks": {"maximum_points": 30.0},
                "feedback_praise": {"points_per_unit": 5.0},
                "feedback_favorite": {"points_per_unit": 5.0},
                "feedback_rebook_15d": {"points_per_unit": 8.0},
                "reliability_on_time": {"points_per_unit": 2.0},
                "reliability_peak": {"points_per_unit": 1.0},
                "classroom_quality": {
                    "points_per_unit": 1.6,
                    "metric": "perfect_cnt",
                    "source_mode": "REAL_TEACHER_SNAPSHOT",
                },
            }
            expected_thresholds = {
                "graduation_raw_score": 100.0,
                "gold_raw_score": 200.0,
                "graduation_external_score": 100.0,
                "gold_external_score": 200.0,
            }
            expected_hard_gates = {
                "graduation": {
                    "required_mandatory_task_count": 10,
                    "maximum_l0_complaint_count": 0,
                },
                "gold": {
                    "required_base_score": 40.0,
                    "minimum_completed_lessons": 10,
                    "minimum_user_feedback_score": 20.0,
                    "maximum_late_count": 1,
                    "maximum_early_count": 0,
                    "maximum_real_absent_count": 0,
                },
            }
            if (
                self.scoring_items.model_dump(mode="json") != expected_scoring_items
                or self.thresholds.model_dump(mode="json") != expected_thresholds
                or self.hard_gates.model_dump(mode="json") != expected_hard_gates
            ):
                raise ValueError(
                    "v5 已冻结课堂质量 perfect_cnt × 1.6、Peak slots 供给里程碑及"
                    "当前资格门槛；再次变化必须发布新的 policy_version"
                )
        if self.policy_version == "v6":
            expected_scoring_items = {
                "capacity": {
                    "milestone_id": "CAPACITY_PEAK_SLOT_40",
                    "metric": "peak_slot_cnt",
                    "operator": "GTE",
                    "threshold": 40,
                    "score_value": 10.0,
                    "maximum_points": 10.0,
                    "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
                },
                "new_teacher_tasks": {"maximum_points": 30.0},
                "feedback_praise": {"points_per_unit": 5.0},
                "feedback_favorite": {"points_per_unit": 5.0},
                "feedback_rebook_15d": {"points_per_unit": 8.0},
                "reliability_on_time": {"points_per_unit": 2.0},
                "reliability_peak": {"points_per_unit": 1.0},
                "classroom_quality": {
                    "points_per_unit": 1.6,
                    "metric": "perfect_cnt",
                    "source_mode": "REAL_TEACHER_SNAPSHOT",
                },
            }
            expected_thresholds = {
                "graduation_raw_score": 100.0,
                "gold_raw_score": 200.0,
                "graduation_external_score": 100.0,
                "gold_external_score": 200.0,
            }
            expected_hard_gates = {
                "graduation": {
                    "required_mandatory_task_count": 10,
                    "maximum_l0_complaint_count": 0,
                },
                "gold": {"inherits_graduation": True},
            }
            if (
                self.scoring_items.model_dump(mode="json") != expected_scoring_items
                or self.thresholds.model_dump(mode="json") != expected_thresholds
                or self.hard_gates.model_dump(mode="json") != expected_hard_gates
            ):
                raise ValueError(
                    "v6 已冻结 perfect_cnt × 1.6、当前出营规则及两项金牌门槛："
                    "满足出营资格且 raw 总分不低于 200；再次变化必须发布新的 policy_version"
                )
        return self


class AgentPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    kill_switch: bool = False
    max_primary_tasks: Literal[1] = 1
    max_secondary_tasks: int = Field(default=2, ge=0, le=2)
    provider: Literal["deterministic", "openai"] = "openai"
    model: str = Field(default="gpt-5.6-terra", min_length=1, max_length=128)
    allow_task_invention: Literal[False] = False

    @field_validator("model")
    @classmethod
    def validate_model_identifier(cls, value: str) -> str:
        normalized = value.strip()
        lowered = normalized.casefold()
        if lowered.startswith("sk-") or lowered.startswith("bearer "):
            raise ValueError("模型字段只能填写模型标识，不能填写凭据")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-")
        if not normalized or any(character not in allowed for character in normalized):
            raise ValueError("模型标识只能包含字母、数字、点、下划线、冒号、斜杠和连字符")
        return normalized

    @property
    def effective_enabled(self) -> bool:
        return self.enabled and not self.kill_switch


class DeliveryPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normal_reminder_minutes_before_due: int = Field(gt=0, le=10080)
    urgent_reminder_minutes_before_due: int = Field(gt=0, le=1440)
    p0_response_window_minutes: int = Field(gt=0, le=1440)
    p0_reminder_minutes_before_response_due: int = Field(gt=0, le=1440)

    @model_validator(mode="after")
    def validate_reminder_order(self) -> "DeliveryPolicyConfig":
        if self.urgent_reminder_minutes_before_due > self.normal_reminder_minutes_before_due:
            raise ValueError("紧急任务提醒提前量不能大于普通任务提醒提前量")
        if self.p0_reminder_minutes_before_response_due >= self.p0_response_window_minutes:
            raise ValueError("P0 提醒提前量必须小于 P0 回复时限")
        return self


CONFIG_SCHEMA_BY_KEY: dict[ConfigKey, type[BaseModel]] = {
    ConfigKey.SCORE_GRADUATION: ScoreGraduationConfig,
    ConfigKey.AGENT_POLICY: AgentPolicyConfig,
    ConfigKey.DELIVERY_POLICY: DeliveryPolicyConfig,
}


SCORE_POLICY_V2_PAYLOAD: dict[str, Any] = {
    "policy_version": "v2",
    "scoring_items": {
        "capacity": {"maximum_points": 10},
        "new_teacher_tasks": {"maximum_points": 30},
        "feedback_praise": {"points_per_unit": 5},
        "feedback_favorite": {"points_per_unit": 5},
        "feedback_rebook_15d": {"points_per_unit": 8},
        "reliability_on_time": {"points_per_unit": 2},
        "reliability_peak": {"points_per_unit": 1},
        "classroom_quality": {
            "points_per_unit": 2,
            "default_achievement_rate": 0.8,
            "source_mode": "MOCK_SIMULATION",
        },
    },
    "thresholds": {
        "graduation_raw_score": 100,
        "gold_raw_score": 660,
        "graduation_external_score": 100,
        "gold_external_score": 200,
    },
    "hard_gates": {
        "graduation": {
            "minimum_base_score": 30,
            "minimum_completed_lessons": 10,
            "minimum_user_feedback_score_exclusive": 0,
            "minimum_reliability_score_exclusive": 0,
            "allow_severe_redline": False,
        },
        "gold": {
            "required_base_score": 40,
            "minimum_completed_lessons": 10,
            "minimum_user_feedback_score": 20,
            "maximum_late_count": 1,
            "maximum_early_count": 0,
            "maximum_real_absent_count": 0,
        },
    },
    "graduation_effect": "IMMEDIATE_ON_CRITERIA",
}


SCORE_POLICY_V3_PAYLOAD: dict[str, Any] = {
    **deepcopy(SCORE_POLICY_V2_PAYLOAD),
    "policy_version": "v3",
    "thresholds": {
        "graduation_raw_score": 100,
        "gold_raw_score": 200,
        "graduation_external_score": 100,
        "gold_external_score": 200,
    },
}


SCORE_POLICY_V4_PAYLOAD: dict[str, Any] = {
    **deepcopy(SCORE_POLICY_V3_PAYLOAD),
    "policy_version": "v4",
    "scoring_items": {
        **deepcopy(SCORE_POLICY_V3_PAYLOAD["scoring_items"]),
        "capacity": {
            "milestone_id": "CAPACITY_PEAK_SLOT_40",
            "metric": "peak_slot_cnt",
            "operator": "GTE",
            "threshold": 40,
            "score_value": 10,
            "maximum_points": 10,
            "settlement_mode": "FIRST_ACHIEVEMENT_LOCKED",
        },
    },
}


SCORE_POLICY_V5_PAYLOAD: dict[str, Any] = {
    **deepcopy(SCORE_POLICY_V4_PAYLOAD),
    "policy_version": "v5",
    "scoring_items": {
        **deepcopy(SCORE_POLICY_V4_PAYLOAD["scoring_items"]),
        "classroom_quality": {
            "metric": "perfect_cnt",
            "points_per_unit": 1.6,
            "source_mode": "REAL_TEACHER_SNAPSHOT",
        },
    },
    "hard_gates": {
        "graduation": {
            "required_mandatory_task_count": 10,
            "maximum_l0_complaint_count": 0,
        },
        "gold": deepcopy(SCORE_POLICY_V4_PAYLOAD["hard_gates"]["gold"]),
    },
}


SCORE_POLICY_V6_PAYLOAD: dict[str, Any] = {
    **deepcopy(SCORE_POLICY_V5_PAYLOAD),
    "policy_version": "v6",
    "hard_gates": {
        "graduation": deepcopy(SCORE_POLICY_V5_PAYLOAD["hard_gates"]["graduation"]),
        "gold": {"inherits_graduation": True},
    },
}


DEFAULT_CONFIG_PAYLOADS: dict[ConfigKey, dict[str, Any]] = {
    ConfigKey.SCORE_GRADUATION: deepcopy(SCORE_POLICY_V6_PAYLOAD),
    ConfigKey.AGENT_POLICY: {
        "enabled": True,
        "kill_switch": False,
        "max_primary_tasks": 1,
        "max_secondary_tasks": 2,
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "allow_task_invention": False,
    },
    ConfigKey.DELIVERY_POLICY: {
        "normal_reminder_minutes_before_due": 60,
        "urgent_reminder_minutes_before_due": 15,
        "p0_response_window_minutes": 120,
        "p0_reminder_minutes_before_response_due": 30,
    },
}


class ConfigVersionRecord(Base):
    __tablename__ = "config_versions"
    __table_args__ = (
        UniqueConstraint("config_key", "version_number", name="uq_config_version_number"),
        Index("ix_config_key_status", "config_key", "status"),
        Index(
            "uq_one_published_config_per_key",
            "config_key",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
            sqlite_where=text("status = 'PUBLISHED'"),
        ),
    )

    version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    config_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    high_impact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, nullable=False, default=list)
    source_version_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("config_versions.version_id"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    validated_by: Mapped[Optional[str]] = mapped_column(String(128))
    published_by: Mapped[Optional[str]] = mapped_column(String(128))
    retired_by: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ConfigPublicationAuditRecord(Base):
    __tablename__ = "config_publication_audits"
    __table_args__ = (Index("ix_config_audit_version_time", "version_id", "occurred_at"),)

    audit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("config_versions.version_id"), nullable=False, index=True
    )
    config_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
