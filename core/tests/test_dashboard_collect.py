"""Behavioral coverage for the read-only Dex Dashboard collector."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COLLECT_SCRIPT = REPO_ROOT / "core" / "dashboard" / "collect.py"
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _collector():
    return importlib.import_module("core.dashboard.collect")


def _fixture_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "messy vault"
    _write(
        vault / "System" / "user-profile.yaml",
        """
name: Alex Example
role: Product lead
company: Example Co
communication:
  formality: professional_casual
  directness: very_direct
  detail_level: concise
analytics:
  enabled: true
entity_creation:
  mode: suggest
journaling:
  morning: true
  evening: false
  weekly: true
capabilities:
  quarter_goals:
    enabled: true
quarterly_planning:
  enabled: false
""".strip()
        + "\n",
    )
    _write(
        vault / "System" / "pillars.yaml",
        """
pillars:
  - id: customers
    name: Customer trust
    description: Make the product dependable.
    keywords: [trust, reliability]
  - id: growth
    name: Sustainable growth
    description: Grow without losing quality.
    keywords: [growth]
""".strip()
        + "\n",
    )
    _write(
        vault / "System" / "integrations" / "config.yaml",
        """
enabled:
  slack: true
  google: false
hooks:
  meeting_prep:
    use_slack: true
    use_google: false
slack:
  configured_at: 2026-07-20T09:00:00Z
  features:
    context: true
  token: do-not-emit
  nested:
    api_key: do-not-emit-either
todoist:
  enabled: true
  configured_at: 2026-07-21
  api_key_env_var: TODOIST_API_KEY
""".strip()
        + "\n",
    )
    _write(
        vault / "System" / "usage_log.md",
        """
## Core Workflows
- [x] Daily planning (`/daily-plan`)
- [ ] Weekly planning (`/week-plan`)
## Meetings
- [x] Meeting prep (`/meeting-prep`)
## Tracking Metadata
- **Setup date:** 2026-07-01
""".strip()
        + "\n",
    )
    analytics = [
        {
            "timestamp": "2026-07-27T08:00:00Z",
            "event": "skill_invoked",
            "properties": {"skill_name": "daily-plan"},
        },
        {
            "timestamp": "2026-07-20T08:00:00+00:00",
            "event": "meeting-prep",
        },
        {
            "timestamp": "2025-01-01T08:00:00Z",
            "event": "old_event",
        },
    ]
    _write(
        vault / "System" / "analytics_log.jsonl",
        "\n".join([json.dumps(analytics[0]), "{malformed", json.dumps(analytics[1]), json.dumps(analytics[2])])
        + "\n",
    )
    _write(
        vault / "System" / "Skill_Ratings" / "ratings.jsonl",
        "\n".join(
            [
                json.dumps({"skill": "daily-plan", "rating": 5}),
                "{malformed",
                json.dumps({"skill": "daily-plan", "rating": 3}),
                json.dumps({"skill": "meeting-prep", "rating": 4}),
            ]
        )
        + "\n",
    )
    _write(
        vault / "System" / "People_Index.json",
        json.dumps(
            {
                "total": 3,
                "people": [
                    {"path": "05-Areas/People/Internal/Alex.md"},
                    {"path": "05-Areas/People/External/Sam.md"},
                    {"path": "05-Areas/People/External/Jo.md"},
                ],
            }
        ),
    )
    _write(
        vault / "System" / "Company_Index.json",
        json.dumps({"total": 2, "companies": [{"name": "One"}, {"name": "Two"}]}),
    )
    _write(
        vault / "System" / ".doctor-last-run.json",
        json.dumps(
            {
                "generated_at": "2026-07-26T10:00:00+00:00",
                "mode": "quick",
                "checks": [
                    {
                        "id": "vault.configs",
                        "feature": "Vault configuration",
                        "verdict": "OK",
                        "detail": "Configuration parses.",
                        "heal": None,
                    },
                    {
                        "id": "calendar",
                        "feature": "Calendar",
                        "verdict": "OFF",
                        "detail": "Not configured.",
                        "heal": None,
                    },
                ],
                "summary": {"ok": 1, "off": 1, "broken": 0, "unknown": 0},
            }
        ),
    )
    _write(vault / "System" / "credentials" / "never-read.json", '{"password":"vault-secret"}\n')
    _write(
        vault / "03-Tasks" / "Tasks.md",
        """
- [x] Finished one ^task-20260701-001 ✅ 2026-07-27 09:30
- [ ] Open one ^task-20260701-002
- [x] Old one ^task-20260701-003 ✅ 2026-07-01 08:00
""".strip()
        + "\n",
    )
    _write(vault / "00-Inbox" / "Meetings" / "2026-07-27" / "customer-sync.md", "# Customer sync\n")
    _write(vault / "00-Inbox" / "Meetings" / "Planning_sync_2026_07_10.md", "# Planning\n")
    undated_meeting = _write(vault / "00-Inbox" / "Meetings" / "Undated.md", "# Undated\n")
    recent_mtime = (NOW - timedelta(days=1)).timestamp()
    os.utime(undated_meeting, (recent_mtime, recent_mtime))
    _write(vault / "00-Inbox" / "Meetings" / "README.md", "# Ignore\n")
    _write(vault / "04-Projects" / "Alpha" / "notes.md", "# Alpha\n")
    old_project = _write(vault / "04-Projects" / "Beta.md", "# Beta\n")
    old_mtime = datetime(2026, 6, 1, 12, tzinfo=timezone.utc).timestamp()
    os.utime(old_project, (old_mtime, old_mtime))
    _write(vault / "04-Projects" / ".hidden.md", "# Ignore\n")
    _write(vault / "Unexpected Top Level" / "noise.txt", "safe to ignore\n")
    skills_list = _write(
        tmp_path / "skills.json",
        json.dumps(["daily-plan", "meeting-prep", "week-plan", "unused-skill"]),
    )
    return vault, skills_list


def test_collects_dashboard_data_without_leaking_secrets(tmp_path: Path) -> None:
    collect = _collector()
    vault, skills_list = _fixture_vault(tmp_path)

    result = collect.collect_dashboard(vault, skills_list=skills_list, now=NOW)

    assert set(result) == {
        "meta",
        "profile",
        "pillars",
        "integrations",
        "usage",
        "analytics",
        "tasks",
        "people",
        "companies",
        "meetings",
        "projects",
        "health",
        "skills",
    }
    assert result["profile"] == {
        "status": "configured",
        "name": "Alex Example",
        "role": "Product lead",
        "company": "Example Co",
        "communication": {
            "formality": "professional_casual",
            "directness": "very_direct",
            "detail_level": "concise",
        },
        "analytics": {"enabled": True},
        "entity_creation": {"mode": "suggest"},
        "journaling": {"morning": True, "evening": False, "weekly": True},
        "quarterly_planning": {"enabled": True},
    }
    assert result["pillars"] == [
        {
            "id": "customers",
            "name": "Customer trust",
            "description": "Make the product dependable.",
        },
        {
            "id": "growth",
            "name": "Sustainable growth",
            "description": "Grow without losing quality.",
        },
    ]
    assert result["integrations"]["enabled_count"] == 2
    assert result["integrations"]["apps"]["slack"] == {
        "enabled": True,
        "configured_at": "2026-07-20T09:00:00+00:00",
        "features": {"context": True, "meeting_prep": True},
    }
    assert result["usage"]["counts"] == {"available": 3, "used": 2}
    assert result["usage"]["journey"]["feature_adoption_score"] >= 2
    assert result["analytics"]["total"] == 3
    assert result["analytics"]["malformed_lines"] == 1
    assert result["analytics"]["by_event"] == {
        "meeting-prep": 1,
        "old_event": 1,
        "skill_invoked": 1,
    }
    assert result["analytics"]["by_iso_week"]["2026-W31"] == 1
    assert result["analytics"]["by_iso_week"]["2026-W30"] == 1
    assert result["tasks"] == {"total": 3, "completed": 2, "completed_last_7_days": 1}
    assert result["people"] == {
        "total": 3,
        "internal": 1,
        "external": 2,
        "source": "index",
    }
    assert result["companies"] == {"total": 2, "source": "index"}
    assert result["meetings"] == {"total": 3, "last_7_days": 2, "last_30_days": 3}
    assert result["projects"] == {"total": 2, "directories": 1, "files": 1}
    assert result["health"]["status"] == "fresh"
    assert result["health"]["summary"] == {"broken": 0, "off": 1, "ok": 1, "unknown": 0}
    assert result["skills"] == {
        "available": ["daily-plan", "meeting-prep", "unused-skill", "week-plan"],
        "used": ["daily-plan", "meeting-prep"],
        "unused": ["unused-skill", "week-plan"],
        "ratings": {
            "daily-plan": {"average": 4.0, "count": 2},
            "meeting-prep": {"average": 4.0, "count": 1},
        },
    }
    assert result["meta"]["vault_age"]["started_on"] == "2026-06-01"
    serialized = json.dumps(result, sort_keys=True).lower()
    for forbidden in (
        "do-not-emit",
        "vault-secret",
        "api_key",
        "password",
        "credential",
        '"token"',
    ):
        assert forbidden not in serialized


def test_missing_and_malformed_sections_degrade_independently(tmp_path: Path) -> None:
    collect = _collector()
    vault = tmp_path / "fresh"
    _write(vault / "System" / "user-profile.yaml", "name: [unfinished\n")
    _write(vault / "System" / "analytics_log.jsonl", "{bad json\n")
    _write(vault / "System" / "People_Index.json", "{bad json\n")
    _write(vault / "05-Areas" / "People" / "Internal" / "One.md", "# One\n")
    _write(vault / "05-Areas" / "People" / "External" / "README.md", "# Ignore\n")
    _write(
        vault / "System" / ".doctor-last-run.json",
        json.dumps(
            {
                "generated_at": "2026-07-01T10:00:00Z",
                "mode": "quick",
                "checks": [],
                "summary": {"ok": 0, "off": 0, "broken": 0, "unknown": 1},
            }
        ),
    )

    result = collect.collect_dashboard(vault, now=NOW)

    assert "error" in result["profile"]
    assert result["people"] == {
        "total": 1,
        "internal": 1,
        "external": 0,
        "source": "filesystem",
    }
    assert result["companies"] == {"total": 0, "source": "filesystem"}
    assert result["meetings"] == {"total": 0, "last_7_days": 0, "last_30_days": 0}
    assert result["tasks"] == {"total": 0, "completed": 0, "completed_last_7_days": 0}
    assert result["analytics"]["total"] == 0
    assert result["analytics"]["malformed_lines"] == 1
    assert result["health"]["status"] == "stale"
    assert result["health"]["guidance"] == "run /dex-doctor for a fresh checkup"


def test_missing_profile_and_doctor_cache_are_explicit(tmp_path: Path) -> None:
    collect = _collector()
    vault = tmp_path / "empty-vault"
    vault.mkdir()

    result = collect.collect_dashboard(vault, now=NOW)

    assert result["profile"]["status"] == "not configured"
    assert result["profile"]["name"] == ""
    assert result["health"] == {
        "label": "cached dex-doctor check",
        "status": "missing",
        "guidance": "run /dex-doctor for a fresh checkup",
    }


def test_yaml_sections_degrade_when_pyyaml_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collect = _collector()
    vault = tmp_path / "vault"
    _write(vault / "System" / "user-profile.yaml", "name: Alex\n")
    monkeypatch.setattr(collect, "yaml", None)

    result = collect.collect_dashboard(vault, now=NOW)

    assert result["profile"] == {"error": "pyyaml unavailable"}


def test_collect_cli_outputs_sorted_json_and_writes_nothing(tmp_path: Path) -> None:
    vault, skills_list = _fixture_vault(tmp_path)
    before = sorted(str(path.relative_to(vault)) for path in vault.rglob("*"))

    completed = subprocess.run(
        [
            sys.executable,
            str(COLLECT_SCRIPT),
            "--vault",
            str(vault),
            "--skills-list",
            str(skills_list),
            "--json",
            "--diagnose",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout)
    assert data["profile"]["name"] == "Alex Example"
    assert '"sections_with_errors": []' in completed.stderr
    assert sorted(str(path.relative_to(vault)) for path in vault.rglob("*")) == before
    assert completed.stdout.index('"analytics"') < completed.stdout.index('"companies"')


def test_collect_cli_rejects_a_nonexistent_vault(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(COLLECT_SCRIPT),
            "--vault",
            str(tmp_path / "missing"),
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "vault is not a directory" in completed.stderr.lower()
    assert completed.stdout == ""
