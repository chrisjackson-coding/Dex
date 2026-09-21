"""A cleared-down task's ID must not be handed out again.

Field report (2026-09-21): generate_task_id() takes max+1 over the IDs it can
find, and 07-Archives was not in the folders it scanned. The weekly review
clears completed tasks out of 03-Tasks/Tasks.md into
07-Archives/Tasks/Completed_*.md, so once the highest-numbered task was
completed and cleared, its number left the scanned set and the next task
created reused it.

Two tasks then share one anchor. update_task_status finds whichever it reaches
first, so completing one can mark the other done, and neither can be repaired
by ID because the ID is no longer unique.

Archived daily plans and reviews under 07-Archives also carry task anchors, so
the folder is the right fix rather than a special case for the completed-task
file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.mcp import work_server


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "03-Tasks").mkdir(parents=True)
    (tmp_path / "07-Archives" / "Tasks").mkdir(parents=True)
    monkeypatch.setattr(work_server, "BASE_DIR", tmp_path)
    return tmp_path


def test_an_archived_completed_task_still_reserves_its_id(vault: Path) -> None:
    """The exact reported shape: highest task completed, cleared, archived."""
    (vault / "03-Tasks" / "Tasks.md").write_text(
        "# Tasks\n\n- [ ] Still open ^task-20260901-100\n", encoding="utf-8"
    )
    (vault / "07-Archives" / "Tasks" / "Completed_2026-09-01.md").write_text(
        "- [x] Cleared down ^task-20260901-101\n", encoding="utf-8"
    )

    assert work_server.generate_task_id().endswith("-102")


def test_an_archived_plan_reserves_the_ids_it_references(vault: Path) -> None:
    """Plans and reviews under 07-Archives carry anchors too."""
    (vault / "03-Tasks" / "Tasks.md").write_text(
        "# Tasks\n\n- [ ] Still open ^task-20260901-100\n", encoding="utf-8"
    )
    plans = vault / "07-Archives" / "Plans"
    plans.mkdir(parents=True)
    (plans / "2026-09-01.md").write_text(
        "- [ ] Focus item ^task-20260901-250\n", encoding="utf-8"
    )

    assert work_server.generate_task_id().endswith("-251")


def test_an_empty_archive_does_not_change_behaviour(vault: Path) -> None:
    (vault / "03-Tasks" / "Tasks.md").write_text(
        "# Tasks\n\n- [ ] Only task ^task-20260901-007\n", encoding="utf-8"
    )

    assert work_server.generate_task_id().endswith("-008")
