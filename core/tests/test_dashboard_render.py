"""Behavioral coverage for the self-contained Dex Dashboard renderer."""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_SCRIPT = REPO_ROOT / "core" / "dashboard" / "render.py"
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _renderer():
    return importlib.import_module("core.dashboard.render")


def _data() -> dict:
    return {
        "meta": {
            "generated_at": "2026-07-27T12:00:00Z",
            "vault_path": "/tmp/private",
            "collector_version": "1",
        },
        "profile": {
            "status": "configured",
            "name": "Alex <Admin>",
            "role": "Product & Strategy",
            "company": "Example Co",
            "communication": {
                "formality": "professional_casual",
                "directness": "very_direct",
                "detail_level": "concise",
            },
        },
        "pillars": [
            {
                "id": "trust",
                "name": "Customer <trust>",
                "description": "Make it dependable.",
            }
        ],
        "integrations": {
            "apps": {
                "Google": {"enabled": False},
                "Slack & Co": {"enabled": True},
            },
            "enabled_count": 1,
        },
        "usage": {"counts": {"available": 4, "used": 2}},
        "analytics": {"total": 7},
        "tasks": {"total": 18, "completed": 12, "completed_last_7_days": 3},
        "people": {"total": 8, "internal": 2, "external": 6},
        "companies": {"total": 0},
        "meetings": {"total": 4, "last_7_days": 2, "last_30_days": 4},
        "projects": {"total": 0},
        "health": {
            "label": "cached dex-doctor check",
            "status": "fresh",
            "generated_at": "2026-07-26T10:00:00Z",
            "mode": "quick",
            "checks": [
                {"id": "vault.configs", "feature": "Vault <configuration>", "verdict": "OK"},
                {"id": "calendar", "feature": "Calendar", "verdict": "OFF"},
                {"id": "search", "feature": "Search", "verdict": "UNKNOWN"},
                {"id": "hooks", "feature": "Hooks", "verdict": "BROKEN"},
            ],
            "summary": {"ok": 1, "off": 1, "unknown": 1, "broken": 1},
        },
        "skills": {
            "available": ["daily-plan", "meeting-prep", "week-plan"],
            "used": ["daily-plan", "meeting-prep"],
            "unused": ["week-plan"],
            "ratings": {},
        },
    }


def _observations() -> dict:
    return {
        "observations": [
            "You completed **3 tasks** with `<care>` [inside Dex](#state).",
            "<script>alert('observation')</script>",
        ],
        "suggestion": {
            "title": "Try <weekly planning>",
            "why": "It connects your pillars & current work.",
            "try_prompt": 'Plan my week around "Customer trust" <today>.',
        },
    }


def test_html_is_self_contained_composed_and_escapes_user_data() -> None:
    render = _renderer()

    page = render.render_dashboard_html(
        _data(),
        _observations(),
        archive_count=7,
        archived=True,
    )

    assert page.startswith("<!doctype html>")
    assert "#0B0F14" in page
    assert "max-width: 880px" in page
    assert '-apple-system, "SF Pro", "Segoe UI", sans-serif' in page
    assert "<script src=" not in page
    assert "<link " not in page
    assert "<img " not in page
    assert re.search(r"""(?:src|href)=["']https?://""", page, re.IGNORECASE) is None
    assert page.index('id="receipt"') < page.index('id="observations"')
    assert page.index('id="observations"') < page.index('id="suggestion"')
    assert page.index('id="suggestion"') < page.index('id="state"')
    assert "Your Dex" in page
    assert "Alex &lt;Admin&gt;" in page
    assert "Monday, July 27, 2026" in page
    assert "2 meetings turned into notes this week" in page
    assert "3 tasks completed this week" in page
    assert "12 completed tasks in Dex" in page
    assert "0 companies" not in page
    assert "<strong>3 tasks</strong>" in page
    assert "<code>&lt;care&gt;</code>" in page
    assert '<a href="#state">inside Dex</a>' in page
    assert "&lt;script&gt;alert(&#x27;observation&#x27;)&lt;/script&gt;" in page
    assert "<script>alert('observation')</script>" not in page
    assert "Try &lt;weekly planning&gt;" in page
    assert 'Plan my week around &quot;Customer trust&quot; &lt;today&gt;.' in page
    assert "navigator.clipboard" in page
    assert "document.execCommand('copy')" in page
    assert "Slack &amp; Co" in page
    assert "connected" in page
    assert "not set up" in page
    assert "needs attention" in page
    assert "broken" not in page.lower()
    assert "Generated locally by Dex · nothing leaves your machine" in page
    assert "snapshot #7 saved" in page


def test_markdown_links_allow_safe_urls_and_reject_javascript() -> None:
    render = _renderer()
    observations = {
        "observations": [
            "[Guide](https://example.test/guide) and [unsafe](javascript:alert(1))"
        ]
    }

    page = render.render_dashboard_html(_data(), observations, archived=False)

    assert '<a href="https://example.test/guide"' in page
    assert 'href="javascript:' not in page
    assert "unsafe (javascript:alert(1))" in page


def test_missing_observations_and_stale_health_degrade_honestly() -> None:
    render = _renderer()
    data = _data()
    data["health"] = {
        "label": "cached dex-doctor check",
        "status": "stale",
        "guidance": "run /dex-doctor for a fresh checkup",
    }

    page = render.render_dashboard_html(data, {}, archived=False)

    assert "Open this from a Dex session to get Dex&#x27;s observations." in page
    assert 'id="suggestion"' not in page
    assert "Run /dex-doctor for a fresh checkup." in page
    assert "snapshot not saved" in page


def test_render_appends_one_compact_snapshot_and_reports_its_number(tmp_path: Path) -> None:
    render = _renderer()
    vault = tmp_path / "vault"
    vault.mkdir()
    output = tmp_path / "dashboard.html"

    result = render.render_dashboard(
        vault,
        _data(),
        _observations(),
        output,
        archive=True,
        now=NOW,
    )

    history = vault / "System" / ".dex" / "dashboard" / "history.jsonl"
    assert result == {"output": str(output), "archived": True, "archive_count": 1}
    assert output.is_file()
    assert history.is_file()
    lines = history.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    snapshot = json.loads(lines[0])
    assert snapshot == {
        "ts": "2026-07-27T12:00:00Z",
        "counts": {
            "tasks_done": 12,
            "people": 8,
            "meetings": 4,
            "skills_used": 2,
            "integrations_on": 1,
        },
        "observations": [
            "You completed **3 tasks** with `<care>` [inside Dex](#state).",
            "<script>alert('observation')</script>",
        ],
        "suggestion_title": "Try <weekly planning>",
    }
    assert "profile" not in snapshot
    assert "analytics" not in snapshot
    assert "snapshot #1 saved" in output.read_text(encoding="utf-8")


def test_no_archive_writes_only_the_requested_html(tmp_path: Path) -> None:
    render = _renderer()
    vault = tmp_path / "vault"
    vault.mkdir()
    output = tmp_path / "preview.html"

    result = render.render_dashboard(
        vault,
        _data(),
        {},
        output,
        archive=False,
        now=NOW,
    )

    assert result == {"output": str(output), "archived": False, "archive_count": 0}
    assert output.is_file()
    assert not (vault / "System" / ".dex" / "dashboard").exists()
    assert "snapshot not saved" in output.read_text(encoding="utf-8")


def test_render_cli_works_without_observations_and_no_archive(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    data_path = tmp_path / "collected.json"
    data_path.write_text(json.dumps(_data()), encoding="utf-8")
    output = tmp_path / "dashboard.html"

    completed = subprocess.run(
        [
            sys.executable,
            str(RENDER_SCRIPT),
            "--vault",
            str(vault),
            "--data",
            str(data_path),
            "--out",
            str(output),
            "--no-archive",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(output)
    assert "Open this from a Dex session" in output.read_text(encoding="utf-8")
    assert not (vault / "System" / ".dex" / "dashboard").exists()


def test_render_cli_rejects_invalid_json_without_a_traceback(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    data_path = tmp_path / "invalid.json"
    data_path.write_text("{invalid", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(RENDER_SCRIPT),
            "--vault",
            str(vault),
            "--data",
            str(data_path),
            "--out",
            str(tmp_path / "dashboard.html"),
            "--no-archive",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "could not read dashboard input" in completed.stderr.lower()
    assert "traceback" not in completed.stderr.lower()
