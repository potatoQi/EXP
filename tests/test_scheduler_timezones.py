"""Timezone-related tests for scheduler state persistence."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from experiment_manager.scheduler.state_store import SchedulerStateStore


def test_state_store_uses_shanghai_timezone(tmp_path: Path) -> None:
    store = SchedulerStateStore(tmp_path)
    state = store.load_state()

    ts = state["updated_at"]
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
    offset = parsed.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 8 * 3600
