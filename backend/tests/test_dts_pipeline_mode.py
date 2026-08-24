from __future__ import annotations

import pytest

from app.dts_pipeline_mode import read_optional_dts_pipeline_mode


class _Connection:
    def __init__(self, relation, mode=None) -> None:
        self.values = [relation, mode]

    def scalar(self, _statement):
        return self.values.pop(0)


def test_pre_v2_database_keeps_legacy_runtime() -> None:
    assert read_optional_dts_pipeline_mode(_Connection(None)) is None


@pytest.mark.parametrize(
    "mode",
    ["V1_COMPAT_DUAL_CAPTURE", "V2_PRIMARY", "ROLLED_BACK"],
)
def test_database_mode_is_authoritative(mode: str) -> None:
    assert read_optional_dts_pipeline_mode(_Connection("control", mode)) == mode


@pytest.mark.parametrize("mode", [None, "", "V1", "UNKNOWN"])
def test_existing_control_without_known_mode_fails_closed(mode) -> None:
    with pytest.raises(RuntimeError, match="DTS_PIPELINE_CONTROL_MODE_INVALID"):
        read_optional_dts_pipeline_mode(_Connection("control", mode))
