"""Render the dashboard's read-only capability journey map."""

from __future__ import annotations

import html
from typing import Any

_CHIP_CLASS = {
    "used": "lit",
    "unused": "dim",
    "available-in-pack": "outlined",
}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _chip(skill: dict[str, Any]) -> str:
    state = str(skill.get("state") or "")
    css_class = _CHIP_CLASS.get(state, "dim")
    name = html.escape(str(skill.get("name") or skill.get("id") or "Unnamed capability"), quote=True)
    description = html.escape(str(skill.get("description") or ""), quote=True)
    label = html.escape(
        f"{skill.get('name') or skill.get('id') or 'Unnamed capability'}: "
        f"{state.replace('-', ' ') or 'unused'}",
        quote=True,
    )
    title = f' title="{description}"' if description else ""
    return f'<li class="journey-chip {css_class}"{title} aria-label="{label}">{name}</li>'


def _territory(group: dict[str, Any]) -> str:
    name = html.escape(str(group.get("name") or "Other"), quote=True)
    skills = [skill for skill in _list(group.get("skills")) if isinstance(skill, dict)]
    chips = "".join(_chip(skill) for skill in skills)
    return f"""
      <div class="state-panel territory">
        <h3>{name}</h3>
        <ul class="journey-chips">{chips}</ul>
      </div>"""


def render_journey(journey: dict) -> str:
    """Render a Nightfall-styled HTML fragment for one capability journey."""
    source = _mapping(journey)
    counts = _mapping(source.get("counts"))
    available = _number(counts.get("available"))
    used = min(_number(counts.get("used")), available)
    groups = [group for group in _list(source.get("groups")) if isinstance(group, dict)]
    body = "".join(_territory(group) for group in groups)
    if not body:
        body = '<p class="quiet">No capabilities are installed in this Dex yet.</p>'
    return f"""
    <section id="journey" aria-labelledby="journey-heading">
      <div class="section-heading">
        <p class="kicker">Your journey</p>
        <h2 id="journey-heading">Your Dex, growing with you</h2>
        <p class="quiet">You use {used} of {available} capabilities.</p>
      </div>
      <div class="state-grid journey-grid">{body}</div>
    </section>"""
