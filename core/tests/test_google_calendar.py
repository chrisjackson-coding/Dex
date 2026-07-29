from __future__ import annotations

import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.integrations.google import calendar

EVENT_KEYS = {
    "title",
    "start",
    "end",
    "location",
    "url",
    "notes",
    "all_day",
    "calendar_identifier",
    "calendar_name",
    "state",
    "current_user_status",
    "last_modified",
}


def _completed(
    payload: dict | str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> SimpleNamespace:
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _query_values(command: list[str]) -> dict[str, str]:
    values = {}
    for index, argument in enumerate(command):
        if argument == "--query":
            key, value = command[index + 1].split("=", 1)
            values[key] = value
    return values


def test_get_events_normalizes_timed_and_all_day_events_to_eventkit_shape(
    monkeypatch,
):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return _completed(
            {
                "summary": "Work Calendar",
                "items": [
                    {
                        "summary": "Weekly planning",
                        "start": {"dateTime": "2026-07-28T09:00:00+01:00"},
                        "end": {"dateTime": "2026-07-28T09:30:00+01:00"},
                        "location": "Studio",
                        "htmlLink": "https://calendar.google.com/event?eid=timed",
                        "description": "Choose the week",
                        "status": "confirmed",
                        "attendees": [
                            {
                                "email": "dave@example.com",
                                "self": True,
                                "responseStatus": "accepted",
                            }
                        ],
                        "updated": "2026-07-27T18:20:00Z",
                    },
                    {
                        "summary": "Company holiday",
                        "start": {"date": "2026-07-28"},
                        "end": {"date": "2026-07-29"},
                        "status": "confirmed",
                    },
                    {
                        "summary": "Cancelled catch-up",
                        "start": {"dateTime": "2026-07-28T14:00:00+01:00"},
                        "end": {"dateTime": "2026-07-28T14:30:00+01:00"},
                        "status": "cancelled",
                    },
                ]
            }
        )

    monkeypatch.setattr(calendar.subprocess, "run", run)
    monkeypatch.setattr(calendar, "get_user_timezone", lambda: ZoneInfo("Europe/London"))

    result = calendar.get_events("2026-07-28")

    assert result == {
        "success": True,
        "calendar": "primary",
        "date_range": "2026-07-28 to 2026-07-29",
        "events": [
            {
                "title": "Weekly planning",
                "start": "2026-07-28T09:00:00+01:00",
                "end": "2026-07-28T09:30:00+01:00",
                "location": "Studio",
                "url": "https://calendar.google.com/event?eid=timed",
                "notes": "Choose the week",
                "all_day": False,
                "calendar_identifier": "primary",
                "calendar_name": "Work Calendar",
                "state": "scheduled",
                "current_user_status": "Accepted",
                "last_modified": "2026-07-27T18:20:00Z",
            },
            {
                "title": "Company holiday",
                "start": "2026-07-28",
                "end": "2026-07-29",
                "location": "",
                "url": "",
                "notes": "",
                "all_day": True,
                "calendar_identifier": "primary",
                "calendar_name": "Work Calendar",
                "state": "scheduled",
                "current_user_status": "Unknown",
                "last_modified": None,
            },
        ],
        "count": 2,
    }
    assert all(set(event) == EVENT_KEYS for event in result["events"])

    command, kwargs = calls[0]
    assert command[:5] == [
        "node",
        str(calendar.DEX_CALL),
        "google",
        "GET",
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
    ]
    assert _query_values(command) == {
        "timeMin": "2026-07-28T00:00:00+01:00",
        "timeMax": "2026-07-29T00:00:00+01:00",
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "250",
    }
    assert command[-1] == "--status"
    assert kwargs["env"]["DEX_VAULT"] == str(calendar.VAULT_ROOT)


def test_get_events_maps_attendee_statuses_and_includes_requested_attendees(
    monkeypatch,
):
    calls = []
    response_statuses = [
        ("Accepted Person", "accepted", "Accepted"),
        ("Declined Person", "declined", "Declined"),
        ("Tentative Person", "tentative", "Tentative"),
        ("Pending Person", "needsAction", "Pending"),
        ("Unknown Person", None, "Unknown"),
    ]
    attendees = [
        {
            "displayName": name,
            "email": f"person-{index}@example.com",
            "responseStatus": google_status,
            "self": google_status == "needsAction",
        }
        for index, (name, google_status, _expected) in enumerate(response_statuses)
    ]
    def run(command, **_kwargs):
        calls.append(command)
        return _completed(
            {
                "items": [
                    {
                        "summary": "Project review",
                        "start": {"dateTime": "2026-07-28T11:00:00Z"},
                        "end": {"dateTime": "2026-07-28T12:00:00Z"},
                        "status": "confirmed",
                        "attendees": attendees,
                    }
                ]
            }
        )

    monkeypatch.setattr(calendar.subprocess, "run", run)
    monkeypatch.setattr(calendar, "get_user_timezone", lambda: ZoneInfo("UTC"))

    result = calendar.get_events(
        "2026-07-28",
        calendar_id="team@example.com",
        with_attendees=True,
    )

    event = result["events"][0]
    assert event["current_user_status"] == "Pending"
    assert event["attendees"] == [
        {
            "name": name,
            "email": f"person-{index}@example.com",
            "status": expected,
        }
        for index, (name, _google_status, expected) in enumerate(response_statuses)
    ]
    assert set(event) == EVENT_KEYS | {"attendees"}
    assert calls[0][4].endswith("/calendars/team%40example.com/events")


def test_list_calendars_mirrors_the_apple_calendar_list_shape(monkeypatch):
    monkeypatch.setattr(
        calendar.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(
            {
                "items": [
                    {"summary": "Dave", "id": "dave@example.com", "primary": True},
                    {"summary": "Team", "id": "team-calendar"},
                ]
            }
        ),
    )

    result = calendar.list_calendars()

    assert result == {
        "success": True,
        "calendars": [
            {
                "title": "Dave",
                "identifier": "dave@example.com",
                "primary": True,
                "type": "google",
            },
            {
                "title": "Team",
                "identifier": "team-calendar",
                "primary": False,
                "type": "google",
            },
        ],
        "count": 2,
    }


def test_get_events_follows_page_tokens_and_reports_the_ten_page_bound(
    monkeypatch,
):
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        page = len(commands)
        return _completed(
            {
                "items": [
                    {
                        "summary": f"Event {page}",
                        "start": {"date": "2026-07-28"},
                        "end": {"date": "2026-07-29"},
                        "status": "confirmed",
                    }
                ],
                "nextPageToken": f"page-{page + 1}",
            }
        )

    monkeypatch.setattr(calendar.subprocess, "run", run)
    monkeypatch.setattr(calendar, "get_user_timezone", lambda: ZoneInfo("UTC"))

    result = calendar.get_events("2026-07-28")

    assert len(commands) == 10
    assert "pageToken" not in _query_values(commands[0])
    assert _query_values(commands[1])["pageToken"] == "page-2"
    assert _query_values(commands[-1])["pageToken"] == "page-10"
    assert result["count"] == 10
    assert result["truncated"] is True
    assert "10 pages" in result["user_message"]


@pytest.mark.parametrize(
    ("returncode", "feature_status", "user_message"),
    [
        (
            2,
            "off",
            "Your Google Calendar isn't connected yet. Run /connect google.",
        ),
        (
            3,
            "broken",
            "Google needs you to sign in again. Run /connect google.",
        ),
    ],
)
def test_connection_errors_are_honest_and_actionable(
    monkeypatch,
    returncode,
    feature_status,
    user_message,
):
    monkeypatch.setattr(
        calendar.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(
            '{"access_token":"ya29.secret","refresh_token":"refresh-secret"}',
            returncode=returncode,
            stderr="Bearer ya29.secret",
        ),
    )

    result = calendar.list_calendars()

    assert result["success"] is False
    assert result["feature"] == "Google Calendar"
    assert result["feature_status"] == feature_status
    assert result["user_message"] == user_message
    assert "ya29.secret" not in json.dumps(result)
    assert "refresh-secret" not in json.dumps(result)


def test_http_errors_include_only_the_safe_status(monkeypatch):
    monkeypatch.setattr(
        calendar.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(
            '{"error":{"code":403},"access_token":"ya29.secret"}',
            returncode=4,
            stderr="403 Forbidden Bearer ya29.secret",
        ),
    )

    result = calendar.list_calendars()
    serialized = json.dumps(result)

    assert result["feature_status"] == "broken"
    assert result["http_status"] == 403
    assert "HTTP 403" in result["user_message"]
    assert "ya29.secret" not in serialized
    assert "Bearer" not in serialized


def test_unexpected_helper_failures_never_return_credential_material(monkeypatch):
    monkeypatch.setattr(
        calendar.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(
            '{"client_secret":"client-secret-value"}',
            returncode=1,
            stderr="refresh_token=refresh-secret-value",
        ),
    )

    result = calendar.list_calendars()
    serialized = json.dumps(result)

    assert result["feature_status"] == "broken"
    assert "secure connection helper" in result["user_message"]
    assert "client-secret-value" not in serialized
    assert "refresh-secret-value" not in serialized


def test_node_missing_reports_broken_without_raising(monkeypatch):
    def missing_node(*_args, **_kwargs):
        raise FileNotFoundError("node")

    monkeypatch.setattr(calendar.subprocess, "run", missing_node)

    result = calendar.list_calendars()

    assert result["feature_status"] == "broken"
    assert "Node.js" in result["user_message"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("connected", True),
        ("expiring", True),
        ("expired", True),
        ("needs_reauth", True),
        ("not_connected", False),
    ],
)
def test_is_connected_uses_the_local_status_surface(
    monkeypatch,
    status,
    expected,
):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return _completed(
            {
                "connections": [
                    {
                        "service": "google",
                        "provider": "google",
                        "status": status,
                    }
                ]
            }
        )

    monkeypatch.setattr(calendar.subprocess, "run", run)

    assert calendar.is_connected() is expected
    assert calls[0][0] == [
        "node",
        str(calendar.CONNECT_CLI),
        "status",
        "--json",
    ]
