"""Build the read-only capability catalog shown in the Dex Dashboard."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core import capabilities as capability_rooms
from core.paths import USER_PROFILE_FILE, VAULT_ROOT

SKILLS_ROOT = Path(".claude") / "skills"
SKILL_FILE = "SKILL.md"
_SKILL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)
_SKILL_COMMAND = re.compile(r"/([a-z0-9][a-z0-9_-]*)", re.IGNORECASE)
_SENTENCE = re.compile(r"^(.+?[.!?])(?:\s|$)")


def _at(vault: Path, configured_path: Path) -> Path:
    """Rebase a core.paths constant from the configured vault to ``vault``."""
    return vault / configured_path.relative_to(VAULT_ROOT)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _skill_id(value: Any) -> str:
    candidate = str(value or "").strip().lower().lstrip("/").replace("_", "-")
    return candidate if _SKILL_ID.fullmatch(candidate) else ""


def _first_sentence(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        return ""
    match = _SENTENCE.match(text)
    return match.group(1) if match else text


def _frontmatter(path: Path) -> dict[str, str]:
    """Read the small metadata subset needed by the dashboard without PyYAML."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return metadata
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        normalized_key = key.strip().lower()
        if normalized_key not in {"name", "description", "category"}:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        metadata[normalized_key] = value
    return {}


def _display_name(skill_id: str, metadata: dict[str, str]) -> str:
    return metadata.get("name") or skill_id.replace("-", " ").title()


def _catalog_entry(path: Path, skill_id: str) -> dict[str, str]:
    metadata = _frontmatter(path)
    return {
        "id": skill_id,
        "name": _display_name(skill_id, metadata),
        "description": _first_sentence(metadata.get("description", "")),
        "category": metadata.get("category", "").strip() or "Other",
    }


def _active_skills(vault: Path) -> dict[str, dict[str, str]]:
    root = vault / SKILLS_ROOT
    if not root.is_dir():
        return {}
    try:
        directories = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return {}

    skills = {}
    for directory in directories:
        skill_id = _skill_id(directory.name)
        skill_path = directory / SKILL_FILE
        if not skill_id or not skill_path.is_file():
            continue
        skills[skill_id] = _catalog_entry(skill_path, skill_id)
    return skills


def _used_skill_ids(data: dict[str, Any]) -> set[str]:
    used = {
        skill_id
        for value in _list(_mapping(data.get("skills")).get("used"))
        if (skill_id := _skill_id(value))
    }

    usage = _mapping(data.get("usage"))
    for feature, completed in _mapping(usage.get("features")).items():
        if completed:
            used.update(_SKILL_COMMAND.findall(str(feature).lower()))
            skill_id = _skill_id(feature)
            if skill_id:
                used.add(skill_id)

    analytics = _mapping(data.get("analytics"))
    for value in _list(analytics.get("skill_names_used")):
        skill_id = _skill_id(value)
        if skill_id:
            used.add(skill_id)
    for event, count in _mapping(analytics.get("by_event")).items():
        if isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0:
            used.update(_SKILL_COMMAND.findall(str(event).lower()))
            skill_id = _skill_id(event)
            if skill_id:
                used.add(skill_id)
    return {_skill_id(skill_id) for skill_id in used if _skill_id(skill_id)}


def _pack_skills(vault: Path, room: str) -> list[tuple[str, Path]]:
    """Find registry-declared pack skills that are physically present in this vault."""
    try:
        declared = _list(capability_rooms.surfaces_for(room).get("skills"))
    except (OSError, ValueError, capability_rooms.CapabilityError):
        return []
    root = vault / capability_rooms.DORMANT_CATALOG / room / "skills"
    skills = []
    for value in declared:
        skill_id = _skill_id(value)
        skill_path = root / skill_id / SKILL_FILE
        if skill_id and skill_path.is_file():
            skills.append((skill_id, skill_path))
    return skills


def _room_states(vault: Path) -> list[dict[str, Any]]:
    profile = _at(vault, USER_PROFILE_FILE)
    try:
        rooms = capability_rooms.room_ids()
    except (OSError, ValueError, capability_rooms.CapabilityError):
        return []
    states = []
    for room in rooms:
        try:
            room_enabled = capability_rooms.enabled(room, profile_path=profile)
        except (OSError, ValueError, capability_rooms.CapabilityError):
            room_enabled = False
        states.append({"id": room, "enabled": room_enabled})
    return states


def _group_name(category: str) -> str:
    return category.strip() or "Other"


def build_journey(vault: Path, data: dict) -> dict:
    """Return a safe, display-ready view of used and available capabilities."""
    vault = Path(vault).expanduser().resolve()
    used = _used_skill_ids(data if isinstance(data, dict) else {})
    catalog = _active_skills(vault)
    packed_states: dict[str, bool] = {}

    rooms = _room_states(vault)
    for room_state in rooms:
        room = room_state["id"]
        room_enabled = room_state["enabled"]
        for skill_id, skill_path in _pack_skills(vault, room):
            if skill_id in catalog:
                continue
            entry = _catalog_entry(skill_path, skill_id)
            if entry["category"] == "Other":
                entry["category"] = room.replace("_", " ").title()
            catalog[skill_id] = entry
            packed_states[skill_id] = room_enabled

    grouped: dict[str, dict[str, Any]] = {}
    for skill_id, entry in sorted(catalog.items()):
        available = packed_states.get(skill_id, True)
        state = "available-in-pack" if not available else ("used" if skill_id in used else "unused")
        category = _group_name(entry["category"])
        key = category.casefold()
        group = grouped.setdefault(
            key,
            {"id": _skill_id(category) or "other", "name": category, "skills": []},
        )
        group["skills"].append(
            {
                "id": skill_id,
                "name": entry["name"],
                "description": entry["description"],
                "state": state,
            }
        )

    groups = [grouped[key] for key in sorted(grouped)]
    for group in groups:
        group["skills"].sort(key=lambda skill: (skill["name"].casefold(), skill["id"]))
    skills = [skill for group in groups for skill in group["skills"]]
    return {
        "groups": groups,
        "counts": {
            "available": sum(skill["state"] != "available-in-pack" for skill in skills),
            "used": sum(skill["state"] == "used" for skill in skills),
        },
        "rooms": rooms,
    }
