#!/usr/bin/env python3
"""
Calendar MCP Server for Dex

Routes reads to the selected Apple or Google calendar provider.
Apple Calendar remains read/write; direct Google Calendar access is read-only.

Tools:
- calendar_list_calendars: List all available calendars
- calendar_set_source: Choose which calendar provider Dex reads
- calendar_get_events: Get events for a date range
- calendar_get_today: Quick access to today's meetings
- calendar_create_event: Create a new event
- calendar_search_events: Search events by title
- calendar_delete_event: Delete an event
- calendar_get_next_event: Get the next upcoming event
- calendar_get_events_with_attendees: Get events with full attendee details
- reminders_list_items: Get incomplete items from a Reminders list
- reminders_complete_item: Mark a Reminder as complete
- reminders_create_item: Create a new Reminder
- reminders_ensure_lists: Create Dex Inbox/Today lists if missing
- reminders_list_completed: Get recently completed Reminders for sync
- reminders_find_and_complete: Find and complete a Reminder by title match
- reminders_clear_completed: Remove completed Reminders from a list
"""

import copy
import hashlib
import json
import logging
import os
import re

# Vault paths (centralized in core.paths)
import subprocess
import sys
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Optional

import mcp.server.stdio
import mcp.types as types
import yaml
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

_repo_root = str(Path(__file__).parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.append(_repo_root)
from core.lifecycle import service as lifecycle_service
from core.paths import PEOPLE_DIR
from core.paths import VAULT_ROOT as VAULT_PATH
from core.transaction.engine import PlanEntry
from core.utils.feature_status import feature_status

# Health system — error queue and health reporting
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from core.utils.dex_logger import log_error as _log_health_error
    from core.utils.dex_logger import mark_healthy as _mark_healthy
    _HAS_HEALTH = True
except ImportError:
    _HAS_HEALTH = False

# Timezone-aware date/time (respects user-profile.yaml timezone)
try:
    import sys as _sys2
    _sys2.path.insert(0, str(Path(__file__).parent.parent.parent))
    from core.utils.timezone import now as _tz_now
    from core.utils.timezone import today as _tz_today
except ImportError:
    def _tz_now():
        return datetime.now()
    def _tz_today():
        return datetime.now().date()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Scripts directory
SCRIPTS_DIR = Path(__file__).parent / "scripts"
CALENDAR_FEATURE = "Calendar access"
REMINDERS_FEATURE = "Reminders access"
CALENDAR_PROVIDERS = {"apple", "google", "none"}
GOOGLE_NEXT_EVENT_WINDOW_DAYS = 14
GOOGLE_SEARCH_MAX_DAYS_EACH_WAY = 365

# User profile path
USER_PROFILE_PATH = VAULT_PATH / "System" / "user-profile.yaml"


class CalendarProfileError(ValueError):
    """Raised when a calendar choice cannot safely update the user profile."""


def get_default_work_calendar() -> str:
    """Get the configured work calendar from user-profile.yaml.
    
    Returns the work_calendar if configured, otherwise tries work_email,
    otherwise falls back to 'Work'.
    
    This dramatically improves performance (45s → 0.3s) by querying
    only the relevant calendar instead of all calendars.
    """
    try:
        import yaml
        if USER_PROFILE_PATH.exists():
            with open(USER_PROFILE_PATH, 'r') as f:
                profile = yaml.safe_load(f)
            
            # Try calendar.work_calendar first
            if profile.get('calendar', {}).get('work_calendar'):
                return profile['calendar']['work_calendar']
            
            # Fall back to work_email
            if profile.get('work_email'):
                return profile['work_email']
            
            # Try constructing from email_domain
            if profile.get('name') and profile.get('email_domain'):
                name = profile['name'].lower().replace(' ', '.')
                domain = profile['email_domain']
                return f"{name}@{domain}"
    except Exception as e:
        logger.warning(f"Could not read work calendar from profile: {e}")
    
    return "Work"  # Fallback default


# Cache the default calendar (read once at startup)
DEFAULT_WORK_CALENDAR = get_default_work_calendar()
logger.info(f"Default work calendar: {DEFAULT_WORK_CALENDAR}")

# Custom JSON encoder for handling date/datetime objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


ALLOWED_SCRIPTS = {
    "calendar_eventkit.py",
    "calendar_create_event.sh",
    "calendar_delete_event.sh",
    "reminders_eventkit.py",
    "check_calendar_permission.py",
    "check_reminders_permission.py",
}


def run_shell_script(script_name: str, *args) -> tuple[bool, str]:
    """Run an allowed shell script from the scripts directory."""
    if script_name not in ALLOWED_SCRIPTS:
        return False, f"Script not allowed: {script_name}"

    script_path = (SCRIPTS_DIR / script_name).resolve()
    if not script_path.is_relative_to(SCRIPTS_DIR.resolve()):
        return False, "Invalid script path"

    if not script_path.exists():
        return False, f"Script not found: {script_name}"

    try:
        # Run the helper under THIS interpreter (the venv python, which has
        # pyobjc EventKit) with the repo root on PYTHONPATH so its
        # `from core.paths import ...` resolves. The script's shebang would
        # otherwise pick up system python3, which lacks EventKit. (adapted from #63)
        env = {**os.environ, "PYTHONPATH": str(VAULT_PATH)}
        result = subprocess.run(
            [sys.executable, str(script_path), *args],
            capture_output=True, text=True, timeout=120, env=env
        )

        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            return False, result.stderr.strip() or f"Exit code: {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"Script '{script_name}' timed out after 120 seconds"
    except Exception as e:
        return False, str(e)


def _broken_feature_payload(feature: str, error: str) -> dict:
    """Preserve a legacy Calendar/Reminders error while adding status fields."""
    return feature_status(feature, "broken", error, error=error)


def _yaml_scalar(value: str) -> str:
    """Render one safe, single-line YAML scalar without reformatting the file."""
    rendered_lines = yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=True,
    ).splitlines()
    if rendered_lines and rendered_lines[-1] == "...":
        rendered_lines.pop()
    if len(rendered_lines) != 1:
        raise CalendarProfileError(
            "Calendar names must fit on one line; calendar source was not changed."
        )
    return rendered_lines[0]


def _split_line_ending(line: str) -> tuple[str, str]:
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def _replace_calendar_value(line: str, key: str, value: str) -> str:
    """Replace one calendar scalar while preserving spacing and inline comments."""
    body, ending = _split_line_ending(line)
    match = re.fullmatch(
        rf"(?P<prefix>  {re.escape(key)}[ \t]*:[ \t]*)(?P<value>.*)",
        body,
    )
    if match is None:
        raise CalendarProfileError(
            "Profile calendar formatting is not safe to edit; "
            "calendar source was not changed."
        )

    existing_value = match.group("value")
    comment_match = re.fullmatch(r".*?(?P<comment>[ \t]+#.*)?", existing_value)
    comment = (
        comment_match.group("comment")
        if comment_match is not None and comment_match.group("comment")
        else ""
    )
    return f"{match.group('prefix')}{_yaml_scalar(value)}{comment}{ending}"


def _set_calendar_values(
    text: str,
    *,
    provider: str,
    calendar_id: Optional[str],
    calendar_exists: bool,
) -> str:
    """Surgically set calendar values while preserving every unrelated byte."""
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    calendar_starts = []
    for index, line in enumerate(lines):
        body, _ending = _split_line_ending(line)
        if re.match(r"^calendar[ \t]*:", body):
            calendar_starts.append(index)

    if not calendar_starts:
        if calendar_exists:
            raise CalendarProfileError(
                "Profile calendar formatting is not safe to edit; "
                "calendar source was not changed."
            )
        suffix = "" if not text or text.endswith(("\n", "\r")) else newline
        addition = f"{suffix}calendar:{newline}  provider: {provider}{newline}"
        if calendar_id is not None:
            addition += f"  work_calendar: {_yaml_scalar(calendar_id)}{newline}"
        return text + addition

    if len(calendar_starts) != 1:
        raise CalendarProfileError(
            "Profile contains more than one calendar section; "
            "calendar source was not changed."
        )

    start = calendar_starts[0]
    calendar_line, _ending = _split_line_ending(lines[start])
    inline_value = calendar_line.split(":", 1)[1].strip()
    if inline_value and not inline_value.startswith("#"):
        raise CalendarProfileError(
            "Profile calendar settings must use an indented block; "
            "calendar source was not changed."
        )
    if not lines[start].endswith(("\n", "\r")):
        lines[start] += newline

    end = len(lines)
    for index in range(start + 1, len(lines)):
        body, _ending = _split_line_ending(lines[index])
        if not body.strip() or body.lstrip().startswith("#"):
            continue
        if body == body.lstrip(" \t"):
            end = index
            break

    desired = {"provider": provider}
    if calendar_id is not None:
        desired["work_calendar"] = calendar_id

    found: dict[str, int] = {}
    for index in range(start + 1, end):
        body, _ending = _split_line_ending(lines[index])
        for key in desired:
            if re.match(rf"^  {re.escape(key)}[ \t]*:", body):
                if key in found:
                    raise CalendarProfileError(
                        f"Profile contains more than one calendar.{key} value; "
                        "calendar source was not changed."
                    )
                found[key] = index

    for key, index in found.items():
        lines[index] = _replace_calendar_value(lines[index], key, desired[key])

    missing = [key for key in desired if key not in found]
    if missing:
        addition = "".join(
            f"  {key}: {_yaml_scalar(desired[key])}{newline}" for key in missing
        )
        lines.insert(start + 1, addition)

    return "".join(lines)


def _profile_vault_root() -> Path:
    """Locate the vault root without accepting a path outside its contract."""
    if (
        USER_PROFILE_PATH.name != "user-profile.yaml"
        or USER_PROFILE_PATH.parent.name != "System"
    ):
        raise CalendarProfileError(
            "Calendar source can only be saved in System/user-profile.yaml."
        )
    return USER_PROFILE_PATH.parent.parent


def _persist_calendar_source(provider: str, calendar_id: Optional[str]) -> None:
    """Commit one verified source via Dex's lifecycle transaction boundary."""
    profile_path = USER_PROFILE_PATH
    if profile_path.is_symlink():
        raise CalendarProfileError(
            "System/user-profile.yaml must not be a symlink; "
            "calendar source was not changed."
        )
    if profile_path.exists() and not profile_path.is_file():
        raise CalendarProfileError(
            "System/user-profile.yaml must be a regular file; "
            "calendar source was not changed."
        )
    try:
        original = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    except OSError as exc:
        raise CalendarProfileError(
            "Dex could not safely read System/user-profile.yaml; "
            "calendar source was not changed."
        ) from exc

    try:
        loaded = yaml.safe_load(original)
    except yaml.YAMLError as exc:
        raise CalendarProfileError(
            "System/user-profile.yaml must contain valid YAML; "
            "calendar source was not changed."
        ) from exc
    profile = {} if loaded is None else loaded
    if not isinstance(profile, dict):
        raise CalendarProfileError(
            "System/user-profile.yaml must contain an object; "
            "calendar source was not changed."
        )

    calendar_exists = "calendar" in profile
    existing_calendar = profile.get("calendar")
    if existing_calendar is not None and not isinstance(existing_calendar, Mapping):
        raise CalendarProfileError(
            "Profile calendar settings must contain an object; "
            "calendar source was not changed."
        )

    expected = copy.deepcopy(profile)
    expected_calendar = expected.get("calendar")
    if not isinstance(expected_calendar, dict):
        expected_calendar = {}
        expected["calendar"] = expected_calendar
    expected_calendar["provider"] = provider
    if calendar_id is not None:
        expected_calendar["work_calendar"] = calendar_id

    updated = _set_calendar_values(
        original,
        provider=provider,
        calendar_id=calendar_id,
        calendar_exists=calendar_exists,
    )
    try:
        reparsed_loaded = yaml.safe_load(updated)
    except yaml.YAMLError as exc:
        raise CalendarProfileError(
            "Calendar profile edit produced invalid YAML; refusing to save."
        ) from exc
    reparsed = {} if reparsed_loaded is None else reparsed_loaded
    if reparsed != expected:
        raise CalendarProfileError(
            "Calendar profile edit changed unrelated profile state; refusing to save."
        )
    if updated == original:
        return

    plan = [
        PlanEntry(
            "System/user-profile.yaml",
            updated.encode("utf-8"),
            mode=(profile_path.stat().st_mode & 0o777) if profile_path.exists() else 0o644,
            expected_current_sha256=(
                hashlib.sha256(original.encode("utf-8")).hexdigest()
                if profile_path.exists()
                else None
            ),
            expected_absent=not profile_path.exists(),
        )
    ]
    vault_root = _profile_vault_root()
    try:
        preview = lifecycle_service._preview_transaction(
            vault_root,
            plan,
            purpose="calendar-source-selection",
            operation="capability-state",
        )
        lifecycle_service._execute_approved_transaction(
            vault_root,
            plan,
            purpose="calendar-source-selection",
            operation="capability-state",
            approved_token=str(preview["approval_token"]),
        )
    except Exception as exc:
        raise CalendarProfileError(
            "Dex could not safely save System/user-profile.yaml; "
            "calendar source was not changed."
        ) from exc


def _available_calendar_titles(result: dict) -> list[str]:
    calendars = result.get("calendars")
    if not isinstance(calendars, list):
        return []
    titles = []
    for calendar in calendars:
        if isinstance(calendar, str):
            titles.append(calendar)
        elif isinstance(calendar, dict) and isinstance(calendar.get("title"), str):
            titles.append(calendar["title"])
    return titles


def _calendar_not_found_payload(
    *,
    provider: str,
    calendar_id: str,
    available_titles: list[str],
) -> dict:
    available = ", ".join(available_titles) if available_titles else "none"
    return {
        "success": False,
        "error": (
            f"Calendar '{calendar_id}' was not found. Available "
            f"{provider.title()} calendars: {available}."
        ),
    }


@cache
def _resolve_calendar_provider(saved_provider: Optional[str] = None) -> str:
    """Resolve the selected provider once, defaulting old/invalid profiles to Apple."""
    provider = saved_provider
    if provider is None:
        try:
            import yaml

            if USER_PROFILE_PATH.exists():
                profile = yaml.safe_load(
                    USER_PROFILE_PATH.read_text(encoding="utf-8")
                ) or {}
                provider = profile.get("calendar", {}).get("provider")
        except Exception as error:
            logger.warning(f"Could not read calendar provider from profile: {error}")

    if provider in CALENDAR_PROVIDERS:
        return provider
    return "apple"


def _get_google_calendar_reader():
    """Load the Google reader lazily so existing Apple-only installs still import."""
    from core.integrations.google import calendar as google_calendar

    return google_calendar


def _calendar_not_connected_payload() -> dict:
    """Return a healthy, non-alarming result for an explicit no-calendar choice."""
    return feature_status(
        CALENDAR_FEATURE,
        "off",
        "No calendar is connected. Connect Apple Calendar or Google Calendar "
        "when you want Dex to use your schedule.",
    )


def _google_calendar_read_only_payload() -> dict:
    """Explain the deliberate read-only boundary without implying a failure."""
    return feature_status(
        "Google calendar changes",
        "off",
        "Dex can read this Google calendar, but it cannot change events because "
        "Google access is read-only. Make this change in Google Calendar instead.",
    )


def _google_events_result(
    *,
    calendar_name: str,
    start_date: str,
    end_date: Optional[str],
    with_attendees: bool,
) -> dict:
    """Call the fixed Google reader boundary and preserve its result envelope."""
    reader = _get_google_calendar_reader()
    return reader.get_events(
        start_date=start_date,
        end_date=end_date,
        calendar_id=calendar_name,
        with_attendees=with_attendees,
    )


def _event_timestamp(event: dict) -> Optional[float]:
    """Return a sortable timestamp for a Google event start value."""
    value = event.get("start")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(
            value.strip().replace("Z", "+00:00").replace(" +0000", "+00:00")
        ).timestamp()
    except ValueError:
        return None


def _get_calendar_list_result() -> dict:
    """Return the same calendar-list payload exposed by the MCP tool."""
    success, output = run_shell_script("calendar_eventkit.py", "list")

    if not success:
        return _broken_feature_payload(CALENDAR_FEATURE, output)

    try:
        calendars = json.loads(output)
        calendar_names = [calendar["title"] for calendar in calendars]
        return {
            "success": True,
            "calendars": calendar_names,
            "count": len(calendar_names),
            "details": calendars,
        }
    except json.JSONDecodeError as e:
        return _broken_feature_payload(CALENDAR_FEATURE, f"JSON parse error: {e}")


@cache
def _get_available_calendar_names() -> Optional[list[str]]:
    """List calendar names once per process for empty-query validation."""
    try:
        result = _get_calendar_list_result()
    except Exception:
        return None

    if not result.get("success"):
        return None

    return result["calendars"]


def _add_missing_calendar_warning(
    result: dict,
    calendar_name: str,
    *,
    event_count: int,
) -> dict:
    """Warn when an empty query targeted a calendar that does not exist."""
    if not result.get("success") or event_count != 0:
        return result

    try:
        calendar_names = _get_available_calendar_names()
    except Exception:
        return result

    if calendar_names is not None and calendar_name not in calendar_names:
        result["warning"] = (
            f"Calendar '{calendar_name}' was not found. Available calendars: "
            f"{calendar_names}. Set calendar.work_calendar in "
            "System/user-profile.yaml."
        )

    return result


def parse_applescript_list(output: str) -> list[str]:
    """Parse comma-separated AppleScript output into a list"""
    if not output:
        return []
    # AppleScript returns lists like: item1, item2, item3
    return [item.strip() for item in output.split(', ') if item.strip()]


def parse_attendee_string(attendee_str: str) -> dict:
    """Parse an attendee string like 'Name<email>[status]' into a dict."""
    match = re.match(r'^(.+?)<(.+?)>\[(.+?)\]$', attendee_str.strip())
    if match:
        name, email, status = match.groups()
        name = name.strip()
        email = email.strip().lower()
        
        # If name equals email, try to extract a proper name from email
        if name.lower() == email.lower() or '@' in name:
            # Extract from email: firstname.lastname@domain -> Firstname Lastname
            local_part = email.split('@')[0]
            name = local_part.replace('.', ' ').replace('_', ' ').replace('-', ' ').title()
        
        return {
            'name': name,
            'email': email,
            'status': status.strip()
        }
    return None


def get_domain_from_email(email: str) -> str:
    """Extract domain from email address."""
    if '@' in email:
        return email.split('@')[1].lower()
    return None


def normalize_name_for_filename(name: str) -> str:
    """Convert a name to a filename-safe format."""
    # Replace spaces with underscores, remove special chars
    safe_name = re.sub(r'[^\w\s-]', '', name)
    safe_name = re.sub(r'\s+', '_', safe_name.strip())
    return safe_name


def find_person_page(name: str, email: str) -> Optional[Path]:
    """Find an existing person page by name or email."""
    # Try multiple name variations
    name_variations = [
        normalize_name_for_filename(name),
        # Also try extracting name from email (firstname.lastname@domain)
        normalize_name_for_filename(email.split('@')[0].replace('.', ' ').replace('_', ' ').title()) if '@' in email else None
    ]
    name_variations = [n for n in name_variations if n]
    
    # Check Internal and External folders
    for folder in ['Internal', 'External']:
        folder_path = PEOPLE_DIR / folder
        if folder_path.exists():
            for file in folder_path.glob('*.md'):
                file_stem_lower = file.stem.lower().replace('_', ' ').replace('-', ' ')
                
                # Check by filename variations
                for name_var in name_variations:
                    name_var_lower = name_var.lower().replace('_', ' ')
                    # Check if names match (allowing for partial matches)
                    if name_var_lower in file_stem_lower or file_stem_lower in name_var_lower:
                        return file
                    # Check individual name parts
                    name_parts = name_var_lower.split()
                    if len(name_parts) >= 2:
                        # Check if first and last name are in filename
                        if name_parts[0] in file_stem_lower and name_parts[-1] in file_stem_lower:
                            return file
                
                # Check by email in file content
                try:
                    content = file.read_text()
                    if email.lower() in content.lower():
                        return file
                except:
                    pass
    return None


# Initialize the MCP server
app = Server("dex-calendar-mcp")


@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List all available calendar tools"""
    return [
        types.Tool(
            name="calendar_list_calendars",
            description="List all calendars available from the selected provider",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="calendar_set_source",
            description="Choose which calendar Dex reads outside of setup",
            inputSchema={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["apple", "google", "none"],
                        "description": "Calendar provider Dex should read",
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": (
                            "Google calendar identifier or Apple calendar name"
                        ),
                    },
                },
                "required": ["provider"],
            },
        ),
        types.Tool(
            name="calendar_get_events",
            description="Get events from a specific calendar for a date range",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_name": {
                        "type": "string",
                        "description": "Name of the calendar (e.g., 'Work' or 'user@example.com')"
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format (defaults to today)"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format (defaults to start_date + 1 day)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default: 50)",
                        "default": 50
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="calendar_get_today",
            description="Quick access to today's events from a calendar",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_name": {
                        "type": "string",
                        "description": "Calendar name (optional, defaults to your work calendar)"
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="calendar_create_event",
            description="Create a new calendar event",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_name": {
                        "type": "string",
                        "description": "Name of the calendar to add the event to"
                    },
                    "title": {
                        "type": "string",
                        "description": "Event title/summary"
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": "Start datetime in 'YYYY-MM-DD HH:MM' format"
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Duration in minutes (default: 30)",
                        "default": 30
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional event description/notes"
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional event location"
                    }
                },
                "required": ["title", "start_datetime"]
            }
        ),
        types.Tool(
            name="calendar_search_events",
            description="Search for events by title across a calendar",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_name": {
                        "type": "string",
                        "description": "Name of the calendar to search"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search term to match against event titles"
                    },
                    "days_back": {
                        "type": "integer",
                        "description": "How many days back to search (default: 30)",
                        "default": 30
                    },
                    "days_forward": {
                        "type": "integer",
                        "description": "How many days forward to search (default: 30)",
                        "default": 30
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="calendar_delete_event",
            description="Delete a calendar event by its title and date",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_name": {
                        "type": "string",
                        "description": "Name of the calendar containing the event"
                    },
                    "title": {
                        "type": "string",
                        "description": "Exact title of the event to delete"
                    },
                    "event_date": {
                        "type": "string",
                        "description": "Date of the event in YYYY-MM-DD format"
                    }
                },
                "required": ["title", "event_date"]
            }
        ),
        types.Tool(
            name="calendar_get_next_event",
            description="Get the next upcoming event from a calendar",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_name": {
                        "type": "string",
                        "description": "Calendar name (optional, defaults to your work calendar)"
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="calendar_get_events_with_attendees",
            description="Get events with full attendee details (name, email, status)",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_name": {
                        "type": "string",
                        "description": "Name of the calendar"
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format (defaults to today)"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format (defaults to start_date + 1 day)"
                    }
                },
                "required": []
            }
        ),
        # --- Apple Reminders tools ---
        types.Tool(
            name="reminders_list_items",
            description="Get incomplete items from an Apple Reminders list. Use with 'Dex Inbox' to check for mobile-captured tasks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the Reminders list (default: 'Dex Inbox')"
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="reminders_complete_item",
            description="Mark a Reminder as complete (e.g., after triaging into Dex tasks)",
            inputSchema={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "string",
                        "description": "The calendarItemIdentifier of the reminder to complete"
                    }
                },
                "required": ["reminder_id"]
            }
        ),
        types.Tool(
            name="reminders_create_item",
            description="Create a new Reminder in a specific list (e.g., push P0 tasks to 'Dex Today' for iOS notifications)",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the Reminders list (default: 'Dex Today')"
                    },
                    "title": {
                        "type": "string",
                        "description": "Reminder title"
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes/description"
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Optional due date in YYYY-MM-DD format"
                    }
                },
                "required": ["title"]
            }
        ),
        types.Tool(
            name="reminders_ensure_lists",
            description="Create 'Dex Inbox' and 'Dex Today' Reminders lists if they don't exist. Idempotent — safe to call every time.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        types.Tool(
            name="reminders_list_completed",
            description="Get recently completed items from a Reminders list (last 2 days). Use with 'Dex Today' to detect tasks completed on phone for sync back to Dex.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the Reminders list (default: 'Dex Today')"
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="reminders_find_and_complete",
            description="Find a Reminder by title match and mark it complete. Use for Dex → Reminders sync when a task is done in Dex.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the Reminders list (default: 'Dex Today')"
                    },
                    "title_query": {
                        "type": "string",
                        "description": "Title text to match against (fuzzy substring match)"
                    }
                },
                "required": ["title_query"]
            }
        ),
        types.Tool(
            name="reminders_clear_completed",
            description="Remove all completed Reminders from a list. Use to clean up 'Dex Today' at start of day.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the Reminders list (default: 'Dex Today')"
                    }
                },
                "required": []
            }
        ),
    ]


@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Handle tool calls"""
    try:
        return await _handle_call_tool_inner(name, arguments)
    except Exception as e:
        if _HAS_HEALTH:
            _tool_human_messages = {
                "calendar_list_calendars": "Calendar listing failed",
                "calendar_set_source": "Calendar source selection failed",
                "calendar_get_events": "Calendar events lookup failed",
                "calendar_get_today": "Today's events lookup failed",
                "calendar_create_event": "Calendar event creation failed",
                "calendar_search_events": "Calendar event search failed",
                "calendar_delete_event": "Calendar event deletion failed",
                "calendar_get_next_event": "Next event lookup failed",
                "calendar_get_events_with_attendees": "Attendee events lookup failed",
            }
            _log_health_error(
                source="calendar-mcp",
                message=str(e),
                human_message=_tool_human_messages.get(name, f"Calendar tool '{name}' failed"),
                context={"tool": name},
            )
        raise


async def _handle_call_tool_inner(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Inner tool handler — wrapped by handle_call_tool for health reporting."""

    global DEFAULT_WORK_CALENDAR

    arguments = arguments or {}
    provider = (
        _resolve_calendar_provider()
        if name.startswith("calendar_") and name != "calendar_set_source"
        else "apple"
    )

    if name == "calendar_set_source":
        requested_provider = arguments.get("provider")
        if isinstance(requested_provider, str):
            requested_provider = requested_provider.strip().lower()
        if requested_provider not in CALENDAR_PROVIDERS:
            result = {
                "success": False,
                "error": "Choose a calendar provider: apple, google, or none.",
            }
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        calendar_id = arguments.get("calendar_id")
        if isinstance(calendar_id, str):
            calendar_id = calendar_id.strip()
        if requested_provider in {"apple", "google"} and (
            not isinstance(calendar_id, str) or not calendar_id
        ):
            result = {
                "success": False,
                "error": (
                    f"calendar_id is required when provider is "
                    f"'{requested_provider}'."
                ),
            }
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        selected_calendar_id = (
            calendar_id if requested_provider in {"apple", "google"} else None
        )
        if requested_provider == "google":
            listing = _get_google_calendar_reader().list_calendars()
            if not listing.get("success"):
                return [
                    types.TextContent(type="text", text=json.dumps(listing, indent=2))
                ]
            calendars = listing.get("calendars")
            identifiers = (
                {
                    calendar.get("identifier")
                    for calendar in calendars
                    if isinstance(calendar, dict)
                }
                if isinstance(calendars, list)
                else set()
            )
            if selected_calendar_id not in identifiers:
                result = _calendar_not_found_payload(
                    provider=requested_provider,
                    calendar_id=selected_calendar_id,
                    available_titles=_available_calendar_titles(listing),
                )
                return [
                    types.TextContent(type="text", text=json.dumps(result, indent=2))
                ]
        elif requested_provider == "apple":
            listing = _get_calendar_list_result()
            if not listing.get("success"):
                return [
                    types.TextContent(type="text", text=json.dumps(listing, indent=2))
                ]
            available_titles = _available_calendar_titles(listing)
            if selected_calendar_id not in available_titles:
                result = _calendar_not_found_payload(
                    provider=requested_provider,
                    calendar_id=selected_calendar_id,
                    available_titles=available_titles,
                )
                return [
                    types.TextContent(type="text", text=json.dumps(result, indent=2))
                ]

        _persist_calendar_source(requested_provider, selected_calendar_id)
        _resolve_calendar_provider.cache_clear()
        _get_available_calendar_names.cache_clear()
        if selected_calendar_id is not None:
            DEFAULT_WORK_CALENDAR = selected_calendar_id

        if requested_provider == "none":
            result = {
                "success": True,
                "provider": "none",
                "message": "Dex will not read from a calendar.",
            }
        else:
            provider_label = requested_provider.title()
            result = {
                "success": True,
                "provider": requested_provider,
                "calendar_id": selected_calendar_id,
                "message": (
                    f"Dex will now read from {provider_label} calendar "
                    f"'{selected_calendar_id}'."
                ),
            }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "calendar_list_calendars":
        if provider == "google":
            result = _get_google_calendar_reader().list_calendars()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        if provider == "none":
            result = _calendar_not_connected_payload()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        result = _get_calendar_list_result()
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "calendar_get_events":
        calendar_name = arguments.get("calendar_name", DEFAULT_WORK_CALENDAR)
        start_date = arguments.get("start_date", _tz_now().strftime("%Y-%m-%d"))

        if provider == "google":
            result = _google_events_result(
                calendar_name=calendar_name,
                start_date=start_date,
                end_date=arguments.get("end_date"),
                with_attendees=False,
            )
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, cls=DateTimeEncoder),
                )
            ]
        if provider == "none":
            result = _calendar_not_connected_payload()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        
        # Parse start date
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        
        # End date defaults to start + 1 day
        if "end_date" in arguments:
            end_dt = datetime.strptime(arguments["end_date"], "%Y-%m-%d")
        else:
            end_dt = start_dt + timedelta(days=1)
        
        # Calculate days offset from today for EventKit
        today_dt = datetime.combine(_tz_today(), datetime.min.time())
        start_offset = (start_dt - today_dt).days
        end_offset = (end_dt - today_dt).days
        
        # Use fast EventKit Python script (replaces slow AppleScript)
        success, output = run_shell_script(
            "calendar_eventkit.py",
            "events",
            calendar_name,
            str(start_offset),
            str(end_offset)
        )
        
        if success:
            # EventKit returns clean JSON
            try:
                events = json.loads(output)
                
                # Filter out all-day events that span beyond the target date
                # (they can pollute results when querying single days)
                filtered_events = []
                for event in events:
                    if event.get('all_day'):
                        # Only include all-day events that start within our range
                        event_start = datetime.fromisoformat(event['start'].replace(' +0000', ''))
                        if start_dt <= event_start < end_dt:
                            filtered_events.append(event)
                    else:
                        # Include all non-all-day events
                        filtered_events.append(event)
                
                result = {
                    "success": True,
                    "calendar": calendar_name,
                    "date_range": f"{start_date} to {end_dt.strftime('%Y-%m-%d')}",
                    "events": filtered_events,
                    "count": len(filtered_events)
                }
            except json.JSONDecodeError as e:
                result = _broken_feature_payload(CALENDAR_FEATURE, f"JSON parse error: {e}")
        else:
            result = _broken_feature_payload(CALENDAR_FEATURE, output)

        result = _add_missing_calendar_warning(
            result,
            calendar_name,
            event_count=result.get("count", -1),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, cls=DateTimeEncoder))]
    
    elif name == "calendar_get_today":
        calendar_name = arguments.get("calendar_name", DEFAULT_WORK_CALENDAR)
        today = _tz_now().strftime("%Y-%m-%d")
        
        # Reuse get_events logic
        arguments = {"calendar_name": calendar_name, "start_date": today}
        return await handle_call_tool("calendar_get_events", arguments)
    
    elif name == "calendar_create_event":
        if provider == "google":
            result = _google_calendar_read_only_payload()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        if provider == "none":
            result = _calendar_not_connected_payload()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        calendar_name = arguments.get("calendar_name", DEFAULT_WORK_CALENDAR)
        title = arguments["title"]
        start_str = arguments["start_datetime"]
        duration = arguments.get("duration_minutes", 30)
        description = arguments.get("description", "")
        location = arguments.get("location", "")
        
        # Validate datetime format
        try:
            datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        except ValueError:
            return [types.TextContent(type="text", text=json.dumps({
                "success": False,
                "error": f"Invalid datetime format. Use 'YYYY-MM-DD HH:MM', got: {start_str}"
            }, indent=2))]
        
        # Use shell script
        success, output = run_shell_script(
            "calendar_create_event.sh",
            calendar_name,
            title,
            start_str,
            str(duration),
            description,
            location
        )
        
        if success:
            result = {
                "success": True,
                "message": output,
                "event": {
                    "title": title,
                    "calendar": calendar_name,
                    "start": start_str,
                    "duration_minutes": duration
                }
            }
        else:
            result = _broken_feature_payload(CALENDAR_FEATURE, output)
        
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, cls=DateTimeEncoder))]
    
    elif name == "calendar_search_events":
        calendar_name = arguments.get("calendar_name", DEFAULT_WORK_CALENDAR)
        query = arguments["query"]
        days_back = arguments.get("days_back", 30)
        days_forward = arguments.get("days_forward", 30)

        if provider == "google":
            # Reader contract has no search endpoint. Bound the local scan to a
            # maximum of one year each way (default: 30 days each way).
            bounded_days_back = max(
                0,
                min(int(days_back), GOOGLE_SEARCH_MAX_DAYS_EACH_WAY),
            )
            bounded_days_forward = max(
                0,
                min(int(days_forward), GOOGLE_SEARCH_MAX_DAYS_EACH_WAY),
            )
            today = _tz_now().date()
            start_date = (today - timedelta(days=bounded_days_back)).isoformat()
            end_date = (
                today + timedelta(days=bounded_days_forward + 1)
            ).isoformat()
            reader_result = _google_events_result(
                calendar_name=calendar_name,
                start_date=start_date,
                end_date=end_date,
                with_attendees=False,
            )
            if not reader_result.get("success"):
                result = reader_result
            else:
                normalized_query = query.casefold()
                events = [
                    event
                    for event in reader_result.get("events", [])
                    if normalized_query in str(event.get("title", "")).casefold()
                ]
                result = {
                    "success": True,
                    "query": query,
                    "calendar": reader_result.get("calendar", calendar_name),
                    "events": events,
                    "count": len(events),
                }
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        if provider == "none":
            result = _calendar_not_connected_payload()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        
        # Use fast EventKit search
        success, output = run_shell_script(
            "calendar_eventkit.py",
            "search",
            calendar_name,
            query,
            str(days_back),
            str(days_forward)
        )
        
        if success:
            try:
                events = json.loads(output)
                result = {
                    "success": True,
                    "query": query,
                    "calendar": calendar_name,
                    "events": events,
                    "count": len(events)
                }
            except json.JSONDecodeError as e:
                result = _broken_feature_payload(CALENDAR_FEATURE, f"JSON parse error: {e}")
        else:
            result = _broken_feature_payload(CALENDAR_FEATURE, output)

        result = _add_missing_calendar_warning(
            result,
            calendar_name,
            event_count=result.get("count", -1),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "calendar_delete_event":
        if provider == "google":
            result = _google_calendar_read_only_payload()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        if provider == "none":
            result = _calendar_not_connected_payload()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        calendar_name = arguments.get("calendar_name", DEFAULT_WORK_CALENDAR)
        title = arguments["title"]
        event_date = arguments["event_date"]
        
        # Parse the date and calculate offset from today
        try:
            target_dt = datetime.strptime(event_date, "%Y-%m-%d")
        except ValueError:
            return [types.TextContent(type="text", text=json.dumps({
                "success": False,
                "error": f"Invalid date format. Use 'YYYY-MM-DD', got: {event_date}"
            }, indent=2))]
        
        today_dt = datetime.combine(_tz_today(), datetime.min.time())
        day_offset = (target_dt - today_dt).days
        
        success, output = run_shell_script(
            "calendar_delete_event.sh",
            calendar_name,
            title,
            str(day_offset)
        )
        
        if success:
            result = {
                "success": True,
                "message": output
            }
        else:
            result = _broken_feature_payload(CALENDAR_FEATURE, output)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "calendar_get_next_event":
        calendar_name = arguments.get("calendar_name", DEFAULT_WORK_CALENDAR)

        if provider == "google":
            # Reader contract has no "next" endpoint. Fourteen days is long
            # enough for normal planning without downloading an open-ended feed.
            now = _tz_now()
            reader_result = _google_events_result(
                calendar_name=calendar_name,
                start_date=now.date().isoformat(),
                end_date=(
                    now.date() + timedelta(days=GOOGLE_NEXT_EVENT_WINDOW_DAYS)
                ).isoformat(),
                with_attendees=False,
            )
            if not reader_result.get("success"):
                result = reader_result
            else:
                now_timestamp = now.timestamp()
                upcoming = [
                    (event_timestamp, event)
                    for event in reader_result.get("events", [])
                    if (event_timestamp := _event_timestamp(event)) is not None
                    and event_timestamp >= now_timestamp
                ]
                upcoming.sort(key=lambda item: item[0])
                if upcoming:
                    result = {
                        "success": True,
                        "next_event": upcoming[0][1],
                    }
                else:
                    result = {
                        "success": True,
                        "message": "No upcoming events",
                        "next_event": None,
                    }
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        if provider == "none":
            result = _calendar_not_connected_payload()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        
        # Use fast EventKit
        success, output = run_shell_script("calendar_eventkit.py", "next", calendar_name)
        
        if success:
            try:
                event_data = json.loads(output)
                if "message" in event_data:
                    # No events found
                    result = {
                        "success": True,
                        "message": event_data["message"],
                        "next_event": None
                    }
                else:
                    # Event found
                    result = {
                        "success": True,
                        "next_event": event_data
                    }
            except json.JSONDecodeError as e:
                result = _broken_feature_payload(CALENDAR_FEATURE, f"JSON parse error: {e}")
        else:
            result = _broken_feature_payload(CALENDAR_FEATURE, output)

        result = _add_missing_calendar_warning(
            result,
            calendar_name,
            event_count=0 if result.get("next_event") is None else 1,
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "calendar_get_events_with_attendees":
        calendar_name = arguments.get("calendar_name", DEFAULT_WORK_CALENDAR)
        start_date = arguments.get("start_date", _tz_now().strftime("%Y-%m-%d"))

        if provider == "google":
            result = _google_events_result(
                calendar_name=calendar_name,
                start_date=start_date,
                end_date=arguments.get("end_date"),
                with_attendees=True,
            )
            if result.get("success"):
                for event in result.get("events", []):
                    for attendee in event.get("attendees") or []:
                        person_page = find_person_page(
                            attendee.get("name", ""),
                            attendee.get("email", ""),
                        )
                        attendee["has_person_page"] = person_page is not None
                        if person_page:
                            attendee["person_page"] = str(
                                person_page.relative_to(VAULT_PATH)
                            )
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, cls=DateTimeEncoder),
                )
            ]
        if provider == "none":
            result = _calendar_not_connected_payload()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        if "end_date" in arguments:
            end_dt = datetime.strptime(arguments["end_date"], "%Y-%m-%d")
        else:
            end_dt = start_dt + timedelta(days=1)
        
        today_dt = datetime.combine(_tz_today(), datetime.min.time())
        start_offset = (start_dt - today_dt).days
        end_offset = (end_dt - today_dt).days
        
        # Use fast EventKit with attendee details
        success, output = run_shell_script(
            "calendar_eventkit.py",
            "attendees",
            calendar_name,
            str(start_offset),
            str(end_offset)
        )
        
        if success:
            try:
                events = json.loads(output)
                
                # Enhance attendees with person page links
                for event in events:
                    if "attendees" in event:
                        for att in event["attendees"]:
                            # Check if person page exists
                            person_page = find_person_page(att.get('name', ''), att.get('email', ''))
                            att['has_person_page'] = person_page is not None
                            if person_page:
                                att['person_page'] = str(person_page.relative_to(VAULT_PATH))
                
                result = {
                    "success": True,
                    "calendar": calendar_name,
                    "date_range": f"{start_date} to {end_dt.strftime('%Y-%m-%d')}",
                    "events": events,
                    "count": len(events)
                }
            except json.JSONDecodeError as e:
                result = _broken_feature_payload(CALENDAR_FEATURE, f"JSON parse error: {e}")
        else:
            result = _broken_feature_payload(CALENDAR_FEATURE, output)

        result = _add_missing_calendar_warning(
            result,
            calendar_name,
            event_count=result.get("count", -1),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, cls=DateTimeEncoder))]
    
    # --- Apple Reminders handlers ---
    elif name == "reminders_list_items":
        list_name = arguments.get("list_name", "Dex Inbox")
        success, output = run_shell_script("reminders_eventkit.py", "list_items", list_name)
        if success:
            try:
                items = json.loads(output)
                result = {"success": True, "list": list_name, "items": items, "count": len(items)}
            except json.JSONDecodeError as e:
                result = _broken_feature_payload(REMINDERS_FEATURE, f"JSON parse error: {e}")
        else:
            result = _broken_feature_payload(REMINDERS_FEATURE, output)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "reminders_complete_item":
        reminder_id = arguments["reminder_id"]
        success, output = run_shell_script("reminders_eventkit.py", "complete", reminder_id)
        if success:
            try:
                result = json.loads(output)
            except json.JSONDecodeError:
                result = {"success": True, "message": output}
        else:
            result = _broken_feature_payload(REMINDERS_FEATURE, output)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "reminders_create_item":
        list_name = arguments.get("list_name", "Dex Today")
        title = arguments["title"]
        notes = arguments.get("notes", "")
        due_date = arguments.get("due_date", "")
        success, output = run_shell_script("reminders_eventkit.py", "create", list_name, title, notes, due_date)
        if success:
            try:
                result = json.loads(output)
            except json.JSONDecodeError:
                result = {"success": True, "message": output}
        else:
            result = _broken_feature_payload(REMINDERS_FEATURE, output)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "reminders_ensure_lists":
        success, output = run_shell_script("reminders_eventkit.py", "ensure_lists")
        if success:
            try:
                result = json.loads(output)
            except json.JSONDecodeError:
                result = {"success": True, "message": output}
        else:
            result = _broken_feature_payload(REMINDERS_FEATURE, output)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "reminders_list_completed":
        list_name = arguments.get("list_name", "Dex Today")
        success, output = run_shell_script("reminders_eventkit.py", "list_completed", list_name)
        if success:
            try:
                items = json.loads(output)
                result = {"success": True, "list": list_name, "items": items, "count": len(items)}
            except json.JSONDecodeError as e:
                result = _broken_feature_payload(REMINDERS_FEATURE, f"JSON parse error: {e}")
        else:
            result = _broken_feature_payload(REMINDERS_FEATURE, output)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "reminders_find_and_complete":
        list_name = arguments.get("list_name", "Dex Today")
        title_query = arguments["title_query"]
        success, output = run_shell_script("reminders_eventkit.py", "find_and_complete", list_name, title_query)
        if success:
            try:
                result = json.loads(output)
            except json.JSONDecodeError:
                result = {"success": True, "message": output}
        else:
            result = _broken_feature_payload(REMINDERS_FEATURE, output)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "reminders_clear_completed":
        list_name = arguments.get("list_name", "Dex Today")
        success, output = run_shell_script("reminders_eventkit.py", "clear_completed", list_name)
        if success:
            try:
                result = json.loads(output)
            except json.JSONDecodeError:
                result = {"success": True, "message": output}
        else:
            result = _broken_feature_payload(REMINDERS_FEATURE, output)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    else:
        return [types.TextContent(type="text", text=json.dumps({
            "error": f"Unknown tool: {name}"
        }, indent=2))]


async def _main():
    """Async main entry point for the MCP server"""
    if _HAS_HEALTH:
        _mark_healthy("calendar-mcp")
    logger.info("Starting Dex Calendar MCP Server")
    logger.info("Using Apple Calendar via AppleScript")
    
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="dex-calendar-mcp",
                server_version="1.0.0",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main():
    """Sync entry point for console script"""
    import asyncio
    asyncio.run(_main())


if __name__ == "__main__":
    main()
