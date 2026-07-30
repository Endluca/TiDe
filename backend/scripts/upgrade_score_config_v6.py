from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config_models import SCORE_POLICY_V6_PAYLOAD, ConfigKey, ScoreGraduationConfig
from app.config_service import ConfigService


LOCAL_APP_ENVS = frozenset({"local", "dev", "development", "test"})


def upgrade_score_config_v6(
    *,
    service: ConfigService | None = None,
    app_env: str | None = None,
    creator_actor_id: str = "system:score-v6-upgrade-creator",
    publisher_actor_id: str = "system:score-v6-upgrade-publisher",
) -> dict:
    """Publish the current two-gate Gold policy without rewriting v5 history."""

    resolved_env = (app_env if app_env is not None else os.getenv("APP_ENV", "")).strip().lower()
    if resolved_env not in LOCAL_APP_ENVS:
        raise RuntimeError(
            "Refusing to upgrade score config outside local/dev/test environments."
        )
    if creator_actor_id == publisher_actor_id:
        raise ValueError("The high-impact score config requires different creator and publisher actors.")

    resolved_service = service or ConfigService()
    current = resolved_service.get_published_payload(ConfigKey.SCORE_GRADUATION)
    try:
        normalized_current = ScoreGraduationConfig.model_validate(current).model_dump(mode="json")
    except (TypeError, ValueError):
        normalized_current = None
    locked_default = ScoreGraduationConfig.model_validate(
        SCORE_POLICY_V6_PAYLOAD
    ).model_dump(mode="json")
    if normalized_current == locked_default:
        return {"status": "SKIPPED_ALREADY_V6", "policy_version": "v6"}

    draft = resolved_service.create_draft(
        ConfigKey.SCORE_GRADUATION,
        actor_id=creator_actor_id,
        payload=deepcopy(SCORE_POLICY_V6_PAYLOAD),
    )
    validation = resolved_service.validate_version(
        draft["version_id"], actor_id=creator_actor_id
    )
    if not validation["valid"]:
        raise RuntimeError(f"Default v6 score config failed validation: {validation['errors']}")
    published = resolved_service.publish_version(
        draft["version_id"], actor_id=publisher_actor_id
    )
    return {
        "status": "UPGRADED",
        "policy_version": published["payload"]["policy_version"],
        "version_id": published["version_id"],
        "version_number": published["version_number"],
    }


def main() -> int:
    try:
        result = upgrade_score_config_v6()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
