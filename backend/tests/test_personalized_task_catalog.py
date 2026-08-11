from __future__ import annotations

from app.task_catalog import task_template_seed_payloads


def test_p_fb_negative_copy_supports_photo_and_other_guided_actions() -> None:
    templates = {
        item["template_id"]: item for item in task_template_seed_payloads()
    }
    template = templates["P-FB-NEGATIVE"]

    assert template["category"] == "PERSONALIZED_IMPROVEMENT"
    assert template["score_type"] == "ZERO"
    assert template["score_value"] == 0
    assert "teaching-environment photo" in template["how_summary"]
    assert "another guided action" in template["how_summary"]
    assert "including any required photo review" in template[
        "completion_standard"
    ]
