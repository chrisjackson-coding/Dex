"""Behavioral coverage for the Dex Dashboard journey catalog."""

from __future__ import annotations

import importlib
from pathlib import Path


def _journey():
    return importlib.import_module("core.dashboard.journey")


def _section():
    return importlib.import_module("core.dashboard.sections.journey")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fake_rooms(monkeypatch, journey, enabled: bool = False) -> None:
    monkeypatch.setattr(journey.capability_rooms, "room_ids", lambda: ("career",))
    monkeypatch.setattr(
        journey.capability_rooms,
        "surfaces_for",
        lambda room: {"skills": ["career-coach"]},
    )
    monkeypatch.setattr(
        journey.capability_rooms,
        "enabled",
        lambda room, *, profile_path: enabled,
    )


def test_build_journey_catalogs_active_and_disabled_pack_skills(tmp_path: Path, monkeypatch) -> None:
    journey = _journey()
    vault = tmp_path / "vault"
    _write(
        vault / ".claude/skills/daily-plan/SKILL.md",
        "---\nname: Daily plan\ndescription: Plan your day around priorities. Later detail.\ncategory: Rituals\n---\n",
    )
    _write(
        vault / ".claude/skills/week-review/SKILL.md",
        "---\nname: Week review\ndescription: Look back at the week\n---\n",
    )
    _write(
        vault / ".claude/skills/focus/SKILL.md",
        "---\nname: Focus\ndescription: Protect <today> from distractions.\ncategory: Rituals\n---\n",
    )
    _write(
        vault / ".claude/skills/malformed/SKILL.md",
        "---\ndescription: [not valid\n---\nThis remains a usable skill.",
    )
    _write(
        vault
        / ".claude/skills/_available/capabilities/career/skills/career-coach/SKILL.md",
        "---\nname: Career coach\ndescription: Build a better career story. Then rehearse it.\ncategory: Career\n---\n",
    )
    _fake_rooms(monkeypatch, journey)

    result = journey.build_journey(
        vault,
        {
            "skills": {"used": ["daily-plan"]},
            "usage": {"features": {"Ran /week-review": True}},
            "analytics": {"skill_names_used": ["focus"]},
        },
    )

    skills = {
        skill["id"]: skill
        for group in result["groups"]
        for skill in group["skills"]
    }
    assert result["counts"] == {"available": 4, "used": 3}
    assert result["rooms"] == [{"id": "career", "enabled": False}]
    assert skills["daily-plan"] == {
        "id": "daily-plan",
        "name": "Daily plan",
        "description": "Plan your day around priorities.",
        "state": "used",
    }
    assert skills["week-review"]["state"] == "used"
    assert skills["focus"]["state"] == "used"
    assert skills["malformed"]["state"] == "unused"
    assert skills["career-coach"] == {
        "id": "career-coach",
        "name": "Career coach",
        "description": "Build a better career story.",
        "state": "available-in-pack",
    }


def test_build_journey_handles_a_vault_without_skills(tmp_path: Path, monkeypatch) -> None:
    journey = _journey()
    _fake_rooms(monkeypatch, journey)

    result = journey.build_journey(tmp_path / "empty-vault", {})

    assert result["groups"] == []
    assert result["counts"] == {"available": 0, "used": 0}
    assert result["rooms"] == [{"id": "career", "enabled": False}]


def test_build_journey_marks_an_enabled_pack_skill_as_available(tmp_path: Path, monkeypatch) -> None:
    journey = _journey()
    vault = tmp_path / "vault"
    _write(
        vault
        / ".claude/skills/_available/capabilities/career/skills/career-coach/SKILL.md",
        "---\nname: Career coach\ndescription: Build a stronger career story.\n---\n",
    )
    _fake_rooms(monkeypatch, journey, enabled=True)

    result = journey.build_journey(vault, {"analytics": {"skill_names_used": ["career-coach"]}})

    assert result["counts"] == {"available": 1, "used": 1}
    assert result["groups"][0]["skills"] == [
        {
            "id": "career-coach",
            "name": "Career coach",
            "description": "Build a stronger career story.",
            "state": "used",
        }
    ]


def test_render_journey_uses_nightfall_states_and_escapes_skill_data() -> None:
    section = _section()

    page = section.render_journey(
        {
            "counts": {"available": 3, "used": 1},
            "groups": [
                {
                    "id": "rituals",
                    "name": "Rituals <script>",
                    "skills": [
                        {
                            "id": "daily-plan",
                            "name": "Daily <plan>",
                            "description": "Plan <today> safely.",
                            "state": "used",
                        },
                        {
                            "id": "week-review",
                            "name": "Week review",
                            "description": "Look back.",
                            "state": "unused",
                        },
                        {
                            "id": "career-coach",
                            "name": "Career coach",
                            "description": "Available after enabling Career.",
                            "state": "available-in-pack",
                        },
                    ],
                }
            ],
        }
    )

    assert 'id="journey"' in page
    assert "You use 1 of 3 capabilities" in page
    assert 'class="journey-chip lit"' in page
    assert 'class="journey-chip dim"' in page
    assert 'class="journey-chip outlined"' in page
    assert "Rituals &lt;script&gt;" in page
    assert "Daily &lt;plan&gt;" in page
    assert "Plan &lt;today&gt; safely." in page
    assert "<script>" not in page
