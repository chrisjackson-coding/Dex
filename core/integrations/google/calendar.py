"""Read Google Calendar through Dex's authenticated connection manager."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from core.paths import VAULT_ROOT
from core.utils.feature_status import feature_status
from core.utils.timezone import get_user_timezone
from core.utils.timezone import now as timezone_now

GOOGLE_CALENDAR_FEATURE = "Google Calendar"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
MAX_PAGES = 10

_CONNECTION_MANAGER_DIR = (
    Path(__file__).resolve().parents[1] / "connection-manager"
)
DEX_CALL = _CONNECTION_MANAGER_DIR / "dex-call.cjs"
CONNECT_CLI = _CONNECTION_MANAGER_DIR / "connect.cjs"

_RESPONSE_STATUS = {
    "accepted": "Accepted",
    "declined": "Declined",
    "tentative": "Tentative",
    "needsAction": "Pending",
}


def _command_environment() -> dict[str, str]:
    """Give the connection manager the vault root without altering global state."""
    return {**os.environ, "DEX_VAULT": str(VAULT_ROOT)}


def _broken(user_message: str, detail: str) -> dict:
    return feature_status(
        GOOGLE_CALENDAR_FEATURE,
        "broken",
        user_message,
        detail=detail,
    )


def _run_command(
    command: list[str],
    *,
    timeout: int,
) -> tuple[subprocess.CompletedProcess[str] | None, dict | None]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_command_environment(),
        )
    except FileNotFoundError:
        return None, _broken(
            "Dex can't read Google Calendar because Node.js isn't available. Run /dex-doctor for help.",
            "The secure connection helper could not start.",
        )
    except subprocess.TimeoutExpired:
        return None, _broken(
            "Google Calendar took too long to respond. Try again.",
            "The secure connection helper timed out.",
        )
    except (OSError, subprocess.SubprocessError):
        return None, _broken(
            "Dex couldn't start the secure connection helper for Google Calendar. Run /dex-doctor for help.",
            "The secure connection helper could not be started.",
        )
    return completed, None


def _http_status(completed: subprocess.CompletedProcess[str]) -> int | None:
    """Extract only an HTTP status, never the provider's raw error payload."""
    match = re.search(r"\b([45]\d{2})\b", completed.stderr or "")
    if match:
        return int(match.group(1))

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    code = payload.get("error", {}).get("code") if isinstance(payload, dict) else None
    return code if isinstance(code, int) and 400 <= code <= 599 else None


def _command_error(completed: subprocess.CompletedProcess[str]) -> dict:
    if completed.returncode == 2:
        return feature_status(
            GOOGLE_CALENDAR_FEATURE,
            "off",
            "Your Google Calendar isn't connected yet. Run /connect google.",
        )
    if completed.returncode == 3:
        return _broken(
            "Google needs you to sign in again. Run /connect google.",
            "The saved Google connection needs re-authentication.",
        )
    if completed.returncode == 4:
        status = _http_status(completed)
        if status is not None:
            return feature_status(
                GOOGLE_CALENDAR_FEATURE,
                "broken",
                f"Google Calendar returned HTTP {status}. Try again; if it keeps happening, run /connect google.",
                detail=f"Google Calendar returned HTTP {status}.",
                http_status=status,
            )
        return _broken(
            "Google Calendar returned an HTTP error. Try again; if it keeps happening, run /connect google.",
            "Google Calendar returned an HTTP error without a readable status.",
        )
    return _broken(
        "Dex couldn't read Google Calendar because the secure connection helper failed. Run /dex-doctor if it keeps happening.",
        f"The secure connection helper exited unexpectedly (code {completed.returncode}).",
    )


def _google_get(
    url: str,
    query: list[tuple[str, str]] | None = None,
) -> tuple[dict | None, dict | None]:
    command = ["node", str(DEX_CALL), "google", "GET", url]
    for key, value in query or []:
        command.extend(["--query", f"{key}={value}"])
    command.append("--status")

    completed, error = _run_command(command, timeout=35)
    if error is not None:
        return None, error
    if completed is None:
        return None, _broken(
            "Dex couldn't read Google Calendar because the secure connection helper failed.",
            "The secure connection helper did not return a result.",
        )
    if completed.returncode != 0:
        return None, _command_error(completed)

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return None, _broken(
            "Google Calendar returned information Dex couldn't read. Try again.",
            "The Google Calendar response was not valid JSON.",
        )
    if not isinstance(payload, dict):
        return None, _broken(
            "Google Calendar returned information Dex couldn't read. Try again.",
            "The Google Calendar response had an unexpected shape.",
        )
    return payload, None


def _items(payload: dict) -> tuple[list[dict] | None, dict | None]:
    items = payload.get("items", [])
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return None, _broken(
            "Google Calendar returned information Dex couldn't read. Try again.",
            "The Google Calendar response had an invalid items list.",
        )
    return items, None


def list_calendars() -> dict:
    """List the user's Google calendars in the same shape as Apple Calendar."""
    payload, error = _google_get(
        f"{GOOGLE_CALENDAR_API}/users/me/calendarList"
    )
    if error is not None:
        return error
    if payload is None:
        return _broken(
            "Google Calendar returned information Dex couldn't read. Try again.",
            "The Google Calendar response was empty.",
        )

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
    return {
        "success": True,
        "calendars": calendars,
        "count": len(calendars),
    }


def _status(response_status: str | None) -> str:
    return _RESPONSE_STATUS.get(response_status, "Unknown")


def _normalize_event(
    event: dict,
    *,
    calendar_id: str,
    calendar_name: str,
    with_attendees: bool,
) -> dict:
    start_data = event.get("start") if isinstance(event.get("start"), dict) else {}
    end_data = event.get("end") if isinstance(event.get("end"), dict) else {}
    all_day = "date" in start_data and "dateTime" not in start_data
    attendees = (
        event.get("attendees")
        if isinstance(event.get("attendees"), list)
        else []
    )

    current_user_status = "Unknown"
    for attendee in attendees:
        if isinstance(attendee, dict) and attendee.get("self") is True:
            current_user_status = _status(attendee.get("responseStatus"))
            break

    normalized = {
        "title": event.get("summary") or "",
        "start": start_data.get("date") if all_day else start_data.get("dateTime"),
        "end": end_data.get("date") if all_day else end_data.get("dateTime"),
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
        normalized["attendees"] = [
            {
                "name": attendee.get("displayName") or "",
                "email": attendee.get("email") or "",
                "status": _status(attendee.get("responseStatus")),
            }
            for attendee in attendees
            if isinstance(attendee, dict)
        ]
    return normalized


def _date_bounds(
    start_date: str,
    end_date: str | None,
) -> tuple[date, date, str, str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date) if end_date is not None else start + timedelta(days=1)
    user_timezone = get_user_timezone()
    if user_timezone is None:
        user_timezone = timezone_now().tzinfo or timezone.utc
    time_min = datetime.combine(start, time.min, tzinfo=user_timezone).isoformat()
    time_max = datetime.combine(end, time.min, tzinfo=user_timezone).isoformat()
    return start, end, time_min, time_max


def get_events(
    start_date: str,
    end_date: str | None = None,
    calendar_id: str = "primary",
    with_attendees: bool = False,
) -> dict:
    """Read and normalize Google Calendar events for an exclusive date range."""
    start, end, time_min, time_max = _date_bounds(start_date, end_date)
    encoded_calendar_id = quote(calendar_id, safe="")
    url = f"{GOOGLE_CALENDAR_API}/calendars/{encoded_calendar_id}/events"
    base_query = [
        ("timeMin", time_min),
        ("timeMax", time_max),
        ("singleEvents", "true"),
        ("orderBy", "startTime"),
        ("maxResults", "250"),
    ]

    events = []
    page_token = None
    for _page in range(MAX_PAGES):
        query = list(base_query)
        if page_token is not None:
            query.append(("pageToken", page_token))
        payload, error = _google_get(url, query)
        if error is not None:
            return error
        if payload is None:
            return _broken(
                "Google Calendar returned information Dex couldn't read. Try again.",
                "The Google Calendar response was empty.",
            )

        items, error = _items(payload)
        if error is not None:
            return error
        calendar_name = payload.get("summary")
        if not isinstance(calendar_name, str) or not calendar_name:
            calendar_name = calendar_id
        events.extend(
            _normalize_event(
                event,
                calendar_id=calendar_id,
                calendar_name=calendar_name,
                with_attendees=with_attendees,
            )
            for event in items or []
            if event.get("status") != "cancelled"
        )

        next_page_token = payload.get("nextPageToken")
        page_token = (
            next_page_token
            if isinstance(next_page_token, str) and next_page_token
            else None
        )
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
        result["user_message"] = (
            "Google Calendar returned more than 10 pages. "
            "Showing the first 10 pages."
        )
    return result


def is_connected() -> bool:
    """Return whether a stored Google credential exists, without a network call."""
    completed, error = _run_command(
        ["node", str(CONNECT_CLI), "status", "--json"],
        timeout=5,
    )
    if error is not None or completed is None or completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    connections = payload.get("connections") if isinstance(payload, dict) else None
    if not isinstance(connections, list):
        return False

    for connection in connections:
        if not isinstance(connection, dict):
            continue
        service = connection.get("service", "")
        is_google = connection.get("provider") == "google" or (
            isinstance(service, str)
            and (service == "google" or service.startswith("google:"))
        )
        if is_google and connection.get("status") not in {None, "not_connected"}:
            return True
    return False
