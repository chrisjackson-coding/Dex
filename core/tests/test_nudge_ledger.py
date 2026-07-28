from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def ledger_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    from core.utils import nudge_ledger

    runtime_dir = tmp_path / "System" / ".dex"
    monkeypatch.setattr(nudge_ledger, "DEX_RUNTIME_DIR", runtime_dir)
    return runtime_dir / "nudge-calendar.json"


def test_record_events_round_trips_and_merges_by_key(ledger_path: Path) -> None:
    from core.utils import nudge_ledger

    first = {
        "key": "dex-one",
        "event_id": "event-1",
        "calendar_id": "primary",
        "date": "2026-07-29",
    }
    second = {
        "key": "dex-two",
        "event_id": "event-2",
        "calendar_id": "primary",
        "date": "2026-07-30",
    }

    recorded = nudge_ledger.record_events([first, second])
    updated = {
        **first,
        "event_id": "event-1-replaced",
        "calendar_id": "work",
    }
    merged = nudge_ledger.record_events([updated])

    assert recorded == {"events": [first, second]}
    assert merged == {"events": [updated, second]}
    assert nudge_ledger.load_events() == [updated, second]
    assert ledger_path.is_file()
    assert list(ledger_path.parent.glob(f".{ledger_path.name}.*.tmp")) == []


def test_failed_atomic_replace_preserves_previous_ledger(
    ledger_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.utils import nudge_ledger

    original = {
        "key": "dex-one",
        "event_id": "event-1",
        "calendar_id": "primary",
        "date": "2026-07-29",
    }
    nudge_ledger.record_events([original])

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(nudge_ledger.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        nudge_ledger.record_events([{**original, "event_id": "event-replaced"}])

    assert nudge_ledger.load_events() == [original]
    assert list(ledger_path.parent.glob(f".{ledger_path.name}.*.tmp")) == []


@pytest.mark.parametrize("contents", [None, "{not-json", '{"events": "wrong"}'])
def test_load_events_returns_empty_for_missing_or_corrupt_ledger(
    ledger_path: Path,
    contents: str | None,
) -> None:
    from core.utils import nudge_ledger

    if contents is not None:
        ledger_path.parent.mkdir(parents=True)
        ledger_path.write_text(contents, encoding="utf-8")

    assert nudge_ledger.load_events() == []


def test_clear_events_reports_count_and_empties_ledger(
    ledger_path: Path,
) -> None:
    from core.utils import nudge_ledger

    entries = [
        {
            "key": f"dex-{index}",
            "event_id": f"event-{index}",
            "calendar_id": "primary",
            "date": f"2026-07-{28 + index}",
        }
        for index in range(1, 3)
    ]
    nudge_ledger.record_events(entries)

    assert nudge_ledger.clear_events() == 2
    assert nudge_ledger.load_events() == []
    assert not ledger_path.exists()


def test_already_created_tracks_whether_the_ledger_has_events(
    ledger_path: Path,
) -> None:
    from core.utils import nudge_ledger

    assert nudge_ledger.already_created() is False

    nudge_ledger.record_events(
        [
            {
                "key": "dex-one",
                "event_id": "event-1",
                "calendar_id": "primary",
                "date": "2026-07-29",
            }
        ]
    )

    assert nudge_ledger.already_created() is True
