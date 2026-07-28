"""Remember calendar events created from Dex's onboarding nudge plan."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from core.paths import DEX_RUNTIME_DIR


def _ledger_path() -> Path:
    return DEX_RUNTIME_DIR / "nudge-calendar.json"


def _atomic_write(payload: dict) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def load_events() -> list[dict]:
    """Return recorded nudge events, or an empty list for unreadable state."""
    try:
        payload = json.loads(_ledger_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return []

    if not isinstance(payload, dict):
        return []
    events = payload.get("events")
    if not isinstance(events, list) or any(
        not isinstance(entry, dict) for entry in events
    ):
        return []
    return [dict(entry) for entry in events]


def record_events(entries: list[dict]) -> dict:
    """Merge created event metadata by stable nudge key and persist it."""
    merged = {
        entry["key"]: entry
        for entry in load_events()
        if isinstance(entry.get("key"), str) and entry["key"]
    }
    for entry in entries:
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("Each nudge event must have a non-empty key")
        merged[key] = dict(entry)

    payload = {"events": list(merged.values())}
    _atomic_write(payload)
    return payload


def clear_events() -> int:
    """Forget all recorded nudge events and return how many were removed."""
    count = len(load_events())
    try:
        _ledger_path().unlink()
    except FileNotFoundError:
        pass
    return count


def already_created() -> bool:
    """Return whether any nudge events have been recorded."""
    return bool(load_events())
