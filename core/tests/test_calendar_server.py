from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from core.mcp import calendar_server


def _decode_tool_result(result):
    return json.loads(result[0].text)


class FakeGoogleCalendarReader:
    def __init__(self):
        self.calls = []

    def list_calendars(self):
        self.calls.append(("list_calendars",))
        return {
            "success": True,
            "calendars": [
                {
                    "title": "jane@acme.com",
                    "identifier": "jane@acme.com",
                    "primary": True,
                    "type": "google",
                }
            ],
            "count": 1,
        }

    def get_events(
        self,
        start_date,
        end_date=None,
        calendar_id="primary",
        with_attendees=False,
    ):
        self.calls.append(
            (
                "get_events",
                start_date,
                end_date,
                calendar_id,
                with_attendees,
            )
        )
        events = [
            {
                "title": "Planning review",
                "start": "2026-07-28T13:00:00",
                "end": "2026-07-28T13:30:00",
                "attendees": [] if with_attendees else None,
            },
            {
                "title": "Customer call",
                "start": "2026-07-29T09:00:00",
                "end": "2026-07-29T09:30:00",
                "attendees": [] if with_attendees else None,
            },
        ]
        return {
            "success": True,
            "calendar": calendar_id,
            "date_range": f"{start_date} to {end_date or start_date}",
            "events": events,
            "count": len(events),
        }


def test_absent_provider_defaults_to_apple_and_keeps_eventkit_path(
    tmp_path,
    monkeypatch,
):
    profile_path = tmp_path / "user-profile.yaml"
    profile_path.write_text(
        "calendar:\n  work_calendar: Work\n",
        encoding="utf-8",
    )
    calls = []

    def fake_run_shell_script(script_name, operation, *args):
        calls.append((script_name, operation, *args))
        return True, json.dumps([{"title": "Work"}])

    monkeypatch.setattr(calendar_server, "USER_PROFILE_PATH", profile_path)
    monkeypatch.setattr(calendar_server, "run_shell_script", fake_run_shell_script)
    monkeypatch.setattr(
        calendar_server,
        "_get_google_calendar_reader",
        lambda: pytest.fail("Google reader should not be loaded"),
        raising=False,
    )
    calendar_server._resolve_calendar_provider.cache_clear()

    payload = _decode_tool_result(
        asyncio.run(
            calendar_server._handle_call_tool_inner("calendar_list_calendars", {})
        )
    )

    assert calendar_server._resolve_calendar_provider() == "apple"
    assert payload == {
        "success": True,
        "calendars": ["Work"],
        "count": 1,
        "details": [{"title": "Work"}],
    }
    assert calls == [("calendar_eventkit.py", "list")]


def test_calendar_set_source_commits_google_selection_through_lifecycle(
    tmp_path,
    monkeypatch,
):
    """Changing a selected provider must use the one transaction boundary."""
    from core.lifecycle import service as lifecycle_service

    profile_path = tmp_path / "System" / "user-profile.yaml"
    profile_path.parent.mkdir()
    profile_path.write_text(
        "calendar:\n  provider: apple\n  work_calendar: Work\n",
        encoding="utf-8",
    )
    reader = FakeGoogleCalendarReader()
    preview_calls = []
    execution_calls = []
    original_preview = lifecycle_service._preview_transaction
    original_execute = lifecycle_service._execute_approved_transaction

    def preview_spy(vault_root, plan, **kwargs):
        preview_calls.append((vault_root, plan, kwargs))
        return original_preview(vault_root, plan, **kwargs)

    def execute_spy(vault_root, plan, **kwargs):
        execution_calls.append((vault_root, plan, kwargs))
        return original_execute(vault_root, plan, **kwargs)

    monkeypatch.setattr(calendar_server, "USER_PROFILE_PATH", profile_path)
    monkeypatch.setattr(calendar_server, "DEFAULT_WORK_CALENDAR", "Work")
    monkeypatch.setattr(calendar_server, "_get_google_calendar_reader", lambda: reader)
    monkeypatch.setattr(
        calendar_server,
        "lifecycle_service",
        lifecycle_service,
        raising=False,
    )
    monkeypatch.setattr(lifecycle_service, "_preview_transaction", preview_spy)
    monkeypatch.setattr(lifecycle_service, "_execute_approved_transaction", execute_spy)
    calendar_server._resolve_calendar_provider.cache_clear()

    payload = _decode_tool_result(
        asyncio.run(
            calendar_server._handle_call_tool_inner(
                "calendar_set_source",
                {"provider": "google", "calendar_id": "jane@acme.com"},
            )
        )
    )

    assert payload["success"] is True
    assert reader.calls == [("list_calendars",)]
    assert execution_calls and len(execution_calls) == 1
    vault_root, plan, kwargs = execution_calls[0]
    assert vault_root == tmp_path
    assert [entry.relative for entry in plan] == ["System/user-profile.yaml"]
    assert kwargs["purpose"] == "calendar-source-selection"
    assert kwargs["operation"] == "capability-state"
    assert preview_calls
    assert profile_path.read_text(encoding="utf-8") == (
        "calendar:\n  provider: google\n  work_calendar: jane@acme.com\n"
    )


def test_calendar_set_source_keeps_apple_on_its_existing_verified_path(
    tmp_path,
    monkeypatch,
):
    profile_path = tmp_path / "System" / "user-profile.yaml"
    profile_path.parent.mkdir()
    profile_path.write_text(
        "calendar:\n  provider: google\n  work_calendar: jane@acme.com\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(calendar_server, "USER_PROFILE_PATH", profile_path)
    monkeypatch.setattr(calendar_server, "DEFAULT_WORK_CALENDAR", "jane@acme.com")
    monkeypatch.setattr(
        calendar_server,
        "_get_calendar_list_result",
        lambda: {
            "success": True,
            "calendars": ["Home", "Team Calendar"],
            "count": 2,
        },
    )
    monkeypatch.setattr(
        calendar_server,
        "_get_google_calendar_reader",
        lambda: pytest.fail("Apple selection must not load the Google reader"),
    )
    calendar_server._resolve_calendar_provider.cache_clear()

    payload = _decode_tool_result(
        asyncio.run(
            calendar_server._handle_call_tool_inner(
                "calendar_set_source",
                {"provider": "apple", "calendar_id": "Team Calendar"},
            )
        )
    )

    assert payload == {
        "success": True,
        "provider": "apple",
        "calendar_id": "Team Calendar",
        "message": "Dex will now read from Apple calendar 'Team Calendar'.",
    }
    assert profile_path.read_text(encoding="utf-8") == (
        "calendar:\n  provider: apple\n  work_calendar: Team Calendar\n"
    )


def test_calendar_set_source_preserves_comments_and_unrelated_profile_bytes(
    tmp_path,
    monkeypatch,
):
    profile_path = tmp_path / "System" / "user-profile.yaml"
    profile_path.parent.mkdir()
    original = (
        "# Keep this profile heading exactly\n"
        "name: Jane  # hand-edited identity\n"
        "calendar:\n"
        "  # This comment explains the choice\n"
        "  provider: apple  # selected source\n"
        "  work_calendar: Work  # do not lose this note\n"
        "  week_starts: monday\n"
        "# Keep this comment below the calendar block\n"
        "timezone: Europe/London  # deliberate ordering\n"
    )
    profile_path.write_text(original, encoding="utf-8")
    reader = FakeGoogleCalendarReader()
    monkeypatch.setattr(calendar_server, "USER_PROFILE_PATH", profile_path)
    monkeypatch.setattr(calendar_server, "_get_google_calendar_reader", lambda: reader)
    calendar_server._resolve_calendar_provider.cache_clear()

    _decode_tool_result(
        asyncio.run(
            calendar_server._handle_call_tool_inner(
                "calendar_set_source",
                {"provider": "google", "calendar_id": "jane@acme.com"},
            )
        )
    )

    assert profile_path.read_text(encoding="utf-8") == original.replace(
        "  provider: apple",
        "  provider: google",
    ).replace(
        "  work_calendar: Work",
        "  work_calendar: jane@acme.com",
    )


def test_calendar_set_source_none_needs_no_provider_and_keeps_prior_calendar(
    tmp_path,
    monkeypatch,
):
    profile_path = tmp_path / "System" / "user-profile.yaml"
    profile_path.parent.mkdir()
    original = "calendar:\n  provider: google\n  work_calendar: jane@acme.com\n"
    profile_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(calendar_server, "USER_PROFILE_PATH", profile_path)
    monkeypatch.setattr(
        calendar_server,
        "_get_google_calendar_reader",
        lambda: pytest.fail("The no-calendar choice must not verify Google"),
    )
    monkeypatch.setattr(
        calendar_server,
        "_get_calendar_list_result",
        lambda: pytest.fail("The no-calendar choice must not verify Apple"),
    )
    calendar_server._resolve_calendar_provider.cache_clear()

    payload = _decode_tool_result(
        asyncio.run(
            calendar_server._handle_call_tool_inner(
                "calendar_set_source",
                {"provider": "none"},
            )
        )
    )

    assert payload == {
        "success": True,
        "provider": "none",
        "message": "Dex will not read from a calendar.",
    }
    assert profile_path.read_text(encoding="utf-8") == (
        "calendar:\n  provider: none\n  work_calendar: jane@acme.com\n"
    )


def test_calendar_set_source_never_claims_google_connected_when_listing_fails(
    tmp_path,
    monkeypatch,
):
    profile_path = tmp_path / "System" / "user-profile.yaml"
    profile_path.parent.mkdir()
    original = "calendar:\n  provider: apple\n  work_calendar: Work\n"
    profile_path.write_text(original, encoding="utf-8")
    not_connected = {
        "success": False,
        "feature": "Google Calendar",
        "feature_status": "off",
        "user_message": "Your Google Calendar isn't connected yet. Run /connect google.",
    }
    monkeypatch.setattr(calendar_server, "USER_PROFILE_PATH", profile_path)
    monkeypatch.setattr(
        calendar_server,
        "_get_google_calendar_reader",
        lambda: SimpleNamespace(list_calendars=lambda: not_connected),
    )
    calendar_server._resolve_calendar_provider.cache_clear()

    payload = _decode_tool_result(
        asyncio.run(
            calendar_server._handle_call_tool_inner(
                "calendar_set_source",
                {"provider": "google", "calendar_id": "jane@acme.com"},
            )
        )
    )

    assert payload == not_connected
    assert profile_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_keys", "with_attendees"),
    [
        (
            "calendar_list_calendars",
            {},
            {"success", "calendars", "count"},
            None,
        ),
        (
            "calendar_get_events",
            {
                "calendar_name": "jane@acme.com",
                "start_date": "2026-07-28",
                "end_date": "2026-07-30",
            },
            {"success", "calendar", "date_range", "events", "count"},
            False,
        ),
        (
            "calendar_get_today",
            {"calendar_name": "jane@acme.com"},
            {"success", "calendar", "date_range", "events", "count"},
            False,
        ),
        (
            "calendar_search_events",
            {
                "calendar_name": "jane@acme.com",
                "query": "planning",
                "days_back": 2,
                "days_forward": 3,
            },
            {"success", "query", "calendar", "events", "count"},
            False,
        ),
        (
            "calendar_get_next_event",
            {"calendar_name": "jane@acme.com"},
            {"success", "next_event"},
            False,
        ),
        (
            "calendar_get_events_with_attendees",
            {
                "calendar_name": "jane@acme.com",
                "start_date": "2026-07-28",
                "end_date": "2026-07-30",
            },
            {"success", "calendar", "date_range", "events", "count"},
            True,
        ),
    ],
)
def test_google_provider_routes_every_read_tool_to_reader(
    monkeypatch,
    tool_name,
    arguments,
    expected_keys,
    with_attendees,
):
    reader = FakeGoogleCalendarReader()
    monkeypatch.setattr(
        calendar_server,
        "_resolve_calendar_provider",
        lambda: "google",
        raising=False,
    )
    monkeypatch.setattr(
        calendar_server,
        "_get_google_calendar_reader",
        lambda: reader,
        raising=False,
    )
    monkeypatch.setattr(
        calendar_server,
        "_tz_now",
        lambda: datetime(2026, 7, 28, 12, 0),
    )
    monkeypatch.setattr(
        calendar_server,
        "run_shell_script",
        lambda *args: pytest.fail("Google reads must not call EventKit"),
    )

    payload = _decode_tool_result(
        asyncio.run(calendar_server._handle_call_tool_inner(tool_name, arguments))
    )

    assert expected_keys <= payload.keys()
    assert payload["success"] is True
    if tool_name == "calendar_list_calendars":
        assert reader.calls == [("list_calendars",)]
    else:
        assert reader.calls[0][0] == "get_events"
        assert reader.calls[0][-1] is with_attendees
    if tool_name == "calendar_search_events":
        assert [event["title"] for event in payload["events"]] == ["Planning review"]
        assert payload["count"] == 1
        assert reader.calls[0][1:4] == (
            "2026-07-26",
            "2026-08-01",
            "jane@acme.com",
        )
    if tool_name == "calendar_get_next_event":
        assert payload["next_event"]["title"] == "Planning review"
        assert reader.calls[0][1:4] == (
            "2026-07-28",
            "2026-08-11",
            "jane@acme.com",
        )


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "calendar_create_event",
            {
                "title": "Planning",
                "start_datetime": "2026-07-28 13:00",
            },
        ),
        (
            "calendar_delete_event",
            {
                "title": "Planning",
                "event_date": "2026-07-28",
            },
        ),
    ],
)
def test_google_write_attempt_is_calmly_refused_without_apple_write(
    monkeypatch,
    tool_name,
    arguments,
):
    monkeypatch.setattr(
        calendar_server,
        "_resolve_calendar_provider",
        lambda: "google",
        raising=False,
    )
    monkeypatch.setattr(
        calendar_server,
        "run_shell_script",
        lambda *args: pytest.fail("Google writes must not call the Apple write path"),
    )

    payload = _decode_tool_result(
        asyncio.run(calendar_server._handle_call_tool_inner(tool_name, arguments))
    )

    assert payload["success"] is False
    assert payload["feature_status"] == "off"
    assert "can read" in payload["user_message"]
    assert "not change" in payload["user_message"]


def test_google_reader_failure_preserves_plain_language_message(monkeypatch):
    user_message = "Connect your Google calendar to see your events."
    reader = SimpleNamespace(
        get_events=lambda *args, **kwargs: {
            "success": False,
            "feature": "Google Calendar",
            "feature_status": "off",
            "user_message": user_message,
        }
    )
    monkeypatch.setattr(
        calendar_server,
        "_resolve_calendar_provider",
        lambda: "google",
        raising=False,
    )
    monkeypatch.setattr(
        calendar_server,
        "_get_google_calendar_reader",
        lambda: reader,
        raising=False,
    )

    payload = _decode_tool_result(
        asyncio.run(
            calendar_server._handle_call_tool_inner(
                "calendar_get_events",
                {"start_date": "2026-07-28"},
            )
        )
    )

    assert payload["user_message"] == user_message


def test_add_missing_calendar_warning_reports_available_calendars(monkeypatch):
    monkeypatch.setattr(
        calendar_server,
        "_get_available_calendar_names",
        lambda: ["Home", "Team Calendar"],
    )
    result = {
        "success": True,
        "calendar": "Guessed Work",
        "events": [],
        "count": 0,
    }

    warned = calendar_server._add_missing_calendar_warning(
        result,
        "Guessed Work",
        event_count=0,
    )

    assert warned["warning"] == (
        "Calendar 'Guessed Work' was not found. Available calendars: "
        "['Home', 'Team Calendar']. Set calendar.work_calendar in "
        "System/user-profile.yaml."
    )


def test_add_missing_calendar_warning_skips_calendar_list_for_nonempty_results(
    monkeypatch,
):
    def fail_if_called():
        raise AssertionError("calendar list should only be fetched for empty results")

    monkeypatch.setattr(
        calendar_server,
        "_get_available_calendar_names",
        fail_if_called,
    )
    result = {"success": True, "events": [{"title": "Standup"}], "count": 1}

    unchanged = calendar_server._add_missing_calendar_warning(
        result,
        "Work",
        event_count=1,
    )

    assert unchanged == result
    assert "warning" not in unchanged


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("calendar_get_events", {}),
        ("calendar_get_today", {}),
        ("calendar_search_events", {"query": "planning"}),
        ("calendar_get_next_event", {}),
        ("calendar_get_events_with_attendees", {}),
    ],
)
def test_empty_calendar_queries_warn_when_default_calendar_is_missing(
    monkeypatch,
    tool_name,
    arguments,
):
    def fake_run_shell_script(script_name, operation, *args):
        assert script_name == "calendar_eventkit.py"
        if operation == "list":
            return True, json.dumps([{"title": "Home"}])
        if operation == "next":
            return True, json.dumps({"message": "No upcoming events"})
        return True, "[]"

    monkeypatch.setattr(calendar_server, "run_shell_script", fake_run_shell_script)
    monkeypatch.setattr(calendar_server, "DEFAULT_WORK_CALENDAR", "Guessed Work")
    calendar_server._get_available_calendar_names.cache_clear()

    payload = _decode_tool_result(
        asyncio.run(calendar_server._handle_call_tool_inner(tool_name, arguments))
    )

    assert payload["warning"] == (
        "Calendar 'Guessed Work' was not found. Available calendars: ['Home']. "
        "Set calendar.work_calendar in System/user-profile.yaml."
    )
