from __future__ import annotations

import os
import time
from pathlib import Path

from scripts.settle_shared_task_scores import (
    _heartbeat_is_fresh,
    _write_heartbeat,
)


def test_score_worker_heartbeat_is_atomic_and_expires(tmp_path: Path) -> None:
    heartbeat = tmp_path / "score-worker-heartbeat"

    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is False
    _write_heartbeat(heartbeat)
    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is True
    assert list(tmp_path.glob(".*.tmp")) == []

    old = time.time() - 30
    os.utime(heartbeat, (old, old))
    assert _heartbeat_is_fresh(heartbeat, max_age_seconds=15) is False
