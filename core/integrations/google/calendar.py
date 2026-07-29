"""Read Google Calendar through Dex's authenticated connection manager.

This module deliberately has no OAuth logic.  It consumes the short-lived access
token held by the connection manager, so calendar code never sees or persists a
credential itself.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from core.paths import VAULT_ROOT
from core.utils.feature_status import feature_status
from core.utils.timezone import get_user_timezone
from core.utils.timezone import now as timezone_now

GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
_CONNECTION_MANAGER = Path(__file__).resolve().parents[1] / "connection-manager"
_DEX_CALL = _CONNECTION_MANAGER / "dex-call.cjs"
_MAX_PAGES = 10
_RESPONSE_STATUS = {
    "accepted": "Accepted",
    "declined": "Declined",
    "tentative": "Tentative",
    "needsAction": "Pending",
}


def _problem(message: str, *, state: str = "broken", detail: str | None = None) -> dict:
    return feature_status("Google Calendar", state, message, detail=detail or message)


def _environment() -> dict[str, str]:
    return {**os.environ, "DEX_VAULT": str(VAULT_ROOT)}


def _get(url: str, query: list[tuple[str, str]] | None = None) -> tuple[dict | None, dict | None]:
    command = ["node", str(_DEX_CALL), "google", "GET", url]
    for key, value in query or []:
        command.extend(["--query", f"{key}={value}"])
    command.append("--status")
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=35,
            env=_environment(),
        )
    except FileNotFoundError:
        return None, _problem("Dex can't read Google Calendar because Node.js isn't available.")
    except subprocess.TimeoutExpired:
        return None, _problem("Google Calendar took too long to respond. Try again.")
    except OSError:
        return None, _problem("Dex couldn't start its secure Google Calendar connection.")

    if completed.returncode == 2:
        return None, _problem("Your Google Calendar isn't connected yet. Run /connect google.", state="off")
    if completed.returncode == 3:
        return None, _problem("Google needs you to sign in again. Run /connect google.", state="off")
    if completed.returncode != 0:
        return None, _problem("Google Calendar couldn't be read. Try again or reconnect Google.")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None, _problem("Google Calendar returned information Dex couldn't read. Try again.")
    if not isinstance(response, dict):
        return None, _problem("Google Calendar returned information Dex couldn't read. Try again.")
    return response, None


def _items(payload: dict) -> tuple[list[dict] | None, dict | None]:
    items = payload.get("items", [])
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return None, _problem("Google Calendar returned an invalid calendar list.")
    return items, None


def list_calendars() -> dict:
    payload, error = _get(f"{GOOGLE_CALENDAR_API}/users/me/calendarList")
    if error is not None:
        return error
    assert payload is not None
    items, error = _items(payload)
    if error is not None:
        return error
    calendars = [
        {
            "title": item.get("summary") or "",
            "identifier": item.get("id") or "",
            "primary": bool(item.get("primary", False)),
            "type": "google",
        }
        for item in items or []
    ]
    return {"success": True, "calendars": calendars, "count": len(calendars)}


def _date_bounds(start_date: str, end_date: str | None) -> tuple[date, date, str, str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date) if end_date else start + timedelta(days=1)
    user_timezone = get_user_timezone() or timezone_now().tzinfo or timezone.utc
    time_min = datetime.combine(start, time.min, tzinfo=user_timezone).isoformat()
    time_max = datetime.combine(end, time.min, tzinfo=user_timezone).isoformat()
    return start, end, time_min, time_max


def _normalise_event(
    event: dict, *, calendar_id: str, calendar_name: str, with_attendees: bool
) -> dict:
    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    end = event.get("end") if isinstance(event.get("end"), dict) else {}
    all_day = "date" in start and "dateTime" not in start
    attendees = event.get("attendees") if isinstance(event.get("attendees"), list) else []
    current_user_status = "Unknown"
    for attendee in attendees:
        if isinstance(attendee, dict) and attendee.get("self") is True:
            current_user_status = _RESPONSE_STATUS.get(attendee.get("responseStatus"), "Unknown")
            break
    result = {
        "title": event.get("summary") or "",
        "start": start.get("date") if all_day else start.get("dateTime"),
        "end": end.get("date") if all_day else end.get("dateTime"),
        "location": event.get("location") or "",
        "url": event.get("htmlLink") or "",
        "notes": event.get("description") or "",
        "all_day": all_day,
        "calendar_identifier": calendar_id,
        "calendar_name": calendar_name,
        "state": "scheduled",
        "current_user_status": current_user_status,
        "last_modified": event.get("updated"),
    }
    if with_attendees:
        result["attendees"] = [
            {
                "name": attendee.get("displayName") or "",
                "email": attendee.get("email") or "",
                "status": _RESPONSE_STATUS.get(attendee.get("responseStatus"), "Unknown"),
            }
            for attendee in attendees
            if isinstance(attendee, dict)
        ]
    return result


def get_events(
    start_date: str,
    end_date: str | None = None,
    calendar_id: str = "primary",
    with_attendees: bool = False,
) -> dict:
    """Read an exclusive date range from one selected Google calendar."""
    start, end, time_min, time_max = _date_bounds(start_date, end_date)
    url = f"{GOOGLE_CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events"
    base_query = [
        ("timeMin", time_min), ("timeMax", time_max), ("singleEvents", "true"),
        ("orderBy", "startTime"), ("maxResults", "250"),
    ]
    events: list[dict] = []
    page_token: str | None = None
    for _ in range(_MAX_PAGES):
        query = list(base_query)
        if page_token:
            query.append(("pageToken", page_token))
        payload, error = _get(url, query)
        if error is not None:
            return error
        assert payload is not None
        items, error = _items(payload)
        if error is not None:
            return error
        calendar_name = payload.get("summary")
        if not isinstance(calendar_name, str) or not calendar_name:
            calendar_name = calendar_id
        events.extend(
            _normalise_event(item, calendar_id=calendar_id, calendar_name=calendar_name, with_attendees=with_attendees)
            for item in items or [] if item.get("status") != "cancelled"
        )
        next_token = payload.get("nextPageToken")
        page_token = next_token if isinstance(next_token, str) and next_token else None
        if page_token is None:
            break
    result = {
        "success": True,
        "calendar": calendar_id,
        "date_range": f"{start.isoformat()} to {end.isoformat()}",
        "events": events,
        "count": len(events),
    }
    if page_token is not None:
        result["truncated"] = True
        result["user_message"] = "Google Calendar returned more than ten pages; Dex is showing the first ten."
    return result
