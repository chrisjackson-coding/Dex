#!/usr/bin/env python3
"""Render one offline Dex Dashboard page and optionally archive a compact snapshot."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.dashboard import history as dashboard_history
from core.dashboard import journey as dashboard_journey
from core.dashboard.sections.history import render_history
from core.dashboard.sections.journey import render_journey
from core.dashboard.sections.settings import render as render_settings
from core.dashboard.server import PORT_PLACEHOLDER, TOKEN_PLACEHOLDER
from core.paths import DEX_RUNTIME_DIR, VAULT_ROOT

INLINE_MARKDOWN = re.compile(
    r"`(?P<code>[^`\n]+)`"
    r"|\[(?P<label>[^\]\n]+)\]\((?P<url>[^)\n]+)\)"
    r"|\*\*(?P<strong>[^*\n]+)\*\*"
)


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_href(value: str) -> str | None:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url
    if parsed.scheme == "mailto" and parsed.path:
        return url
    if not parsed.scheme and url.startswith(("#", "/", "./", "../")):
        return url
    return None


def _inline_markdown(value: str) -> str:
    output = []
    cursor = 0
    for match in INLINE_MARKDOWN.finditer(value):
        output.append(_escape(value[cursor : match.start()]))
        if match.group("code") is not None:
            output.append(f"<code>{_escape(match.group('code'))}</code>")
        elif match.group("strong") is not None:
            output.append(f"<strong>{_escape(match.group('strong'))}</strong>")
        else:
            label = _escape(match.group("label"))
            raw_url = match.group("url")
            href = _safe_href(raw_url)
            if href is None:
                output.append(f"{label} ({_escape(raw_url)})")
            else:
                rel = ' rel="noreferrer"' if urlparse(href).scheme in {"http", "https"} else ""
                output.append(f'<a href="{_escape(href)}"{rel}>{label}</a>')
        cursor = match.end()
    output.append(_escape(value[cursor:]))
    return "".join(output)


def _markdown(value: str) -> str:
    paragraphs = []
    for part in re.split(r"\n\s*\n", value.strip()):
        if not part:
            continue
        paragraphs.append(f"<p>{_inline_markdown(part).replace(chr(10), '<br>')}</p>")
    return "".join(paragraphs)


def _number(section: Any, key: str) -> int:
    value = _mapping(section).get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _receipt_lines(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    meetings_week = _number(data.get("meetings"), "last_7_days")
    tasks_week = _number(data.get("tasks"), "completed_last_7_days")
    meetings_total = _number(data.get("meetings"), "total")
    tasks_done = _number(data.get("tasks"), "completed")
    people = _number(data.get("people"), "total")
    skills_used = len(
        [name for name in _list(_mapping(data.get("skills")).get("used")) if isinstance(name, str)]
    )
    weekly = []
    if meetings_week:
        weekly.append(
            f"{meetings_week} {_plural(meetings_week, 'meeting')} turned into notes this week"
        )
    if tasks_week:
        weekly.append(f"{tasks_week} {_plural(tasks_week, 'task')} completed this week")
    all_time = []
    if meetings_total:
        all_time.append(f"{meetings_total} {_plural(meetings_total, 'meeting note')} in Dex")
    if tasks_done:
        all_time.append(f"{tasks_done} completed {_plural(tasks_done, 'task')} in Dex")
    if people:
        all_time.append(f"{people} {_plural(people, 'person', 'people')} in Dex")
    if skills_used:
        all_time.append(f"{skills_used} {_plural(skills_used, 'skill')} used")
    return weekly, all_time


def _render_receipt(data: dict[str, Any]) -> str:
    weekly, all_time = _receipt_lines(data)
    groups = []
    if weekly:
        groups.append(
            '<div class="receipt-group"><h3>This week</h3>'
            + "".join(f"<p>{_escape(line)}</p>" for line in weekly)
            + "</div>"
        )
    if all_time:
        groups.append(
            '<div class="receipt-group"><h3>All time</h3>'
            + "".join(f"<p>{_escape(line)}</p>" for line in all_time)
            + "</div>"
        )
    body = "".join(groups)
    if not body:
        body = '<p class="quiet">Your value receipt will grow as you use Dex.</p>'
    return f"""
    <section id="receipt" aria-labelledby="receipt-heading">
      <div class="section-heading">
        <p class="kicker">Value receipt</p>
        <h2 id="receipt-heading">What Dex has held onto for you</h2>
      </div>
      <div class="receipt-grid">{body}</div>
    </section>"""


def _observation_strings(observations: Any) -> list[str]:
    return [
        item
        for item in _list(_mapping(observations).get("observations"))
        if isinstance(item, str) and item.strip()
    ]


def _render_observations(observations: Any) -> str:
    items = _observation_strings(observations)
    if items:
        body = "".join(f'<div class="observation">{_markdown(item)}</div>' for item in items)
    else:
        body = (
            '<p class="quiet">Open this from a Dex session to get Dex&#x27;s '
            "observations.</p>"
        )
    return f"""
    <section id="observations" aria-labelledby="observations-heading">
      <div class="section-heading">
        <p class="kicker">A view from Dex</p>
        <h2 id="observations-heading">What stands out</h2>
      </div>
      <div class="prose">{body}</div>
    </section>"""


def _suggestion(observations: Any) -> dict[str, str]:
    raw = _mapping(_mapping(observations).get("suggestion"))
    return {
        key: str(raw.get(key) or "").strip()
        for key in ("title", "why", "try_prompt")
    }


def _render_suggestion(observations: Any) -> str:
    suggestion = _suggestion(observations)
    if not any(suggestion.values()):
        return ""
    title = suggestion["title"] or "A useful next step"
    why = (
        f'<p class="suggestion-why">{_escape(suggestion["why"])}</p>'
        if suggestion["why"]
        else ""
    )
    prompt = ""
    if suggestion["try_prompt"]:
        prompt = f"""
        <div class="try-block">
          <div class="try-label">Try it</div>
          <pre id="tryPrompt"><code>{_escape(suggestion["try_prompt"])}</code></pre>
          <button type="button" id="copyPrompt">Copy prompt</button>
          <span class="copy-status" id="copyStatus" aria-live="polite"></span>
        </div>"""
    return f"""
    <section id="suggestion" class="suggestion" aria-labelledby="suggestion-heading">
      <p class="kicker">One next step</p>
      <h2 id="suggestion-heading">{_escape(title)}</h2>
      {why}
      {prompt}
    </section>"""


def _pretty_setting(value: Any) -> str:
    return str(value).replace("_", " ").strip()


def _render_profile_state(data: dict[str, Any]) -> str:
    profile = _mapping(data.get("profile"))
    role = str(profile.get("role") or "").strip()
    communication = _mapping(profile.get("communication"))
    communication_parts = [
        _pretty_setting(communication[key])
        for key in ("formality", "directness", "detail_level")
        if communication.get(key) not in (None, "")
    ]
    pillars = [
        str(item.get("name") or "").strip()
        for item in _list(data.get("pillars"))
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    rows = []
    if role:
        rows.append(f"<div><dt>Role</dt><dd>{_escape(role)}</dd></div>")
    if pillars:
        rows.append(
            f"<div><dt>Pillars</dt><dd>{_escape(', '.join(pillars))}</dd></div>"
        )
    if communication_parts:
        rows.append(
            "<div><dt>Communication style</dt>"
            f"<dd>{_escape(', '.join(communication_parts))}</dd></div>"
        )
    if not rows:
        return '<p class="quiet">Your role and preferences are not configured yet.</p>'
    return f'<dl class="state-list">{"".join(rows)}</dl>'


def _render_integrations(data: dict[str, Any]) -> str:
    apps = _mapping(_mapping(data.get("integrations")).get("apps"))
    rows = []
    for name, raw in sorted(apps.items(), key=lambda item: str(item[0]).casefold()):
        enabled = bool(_mapping(raw).get("enabled"))
        state = "connected" if enabled else "not set up"
        css = "on" if enabled else "off"
        rows.append(
            '<li>'
            f'<span class="dot dot-{css}" aria-hidden="true"></span>'
            f'<span class="integration-name">{_escape(name)}</span>'
            f'<span class="integration-state">{state}</span>'
            "</li>"
        )
    if not rows:
        return '<p class="quiet">No integration setup is recorded.</p>'
    return f'<ul class="integration-list">{"".join(rows)}</ul>'


def _health_label(verdict: str) -> tuple[str, str]:
    return {
        "OK": ("good", "looking good"),
        "OFF": ("off", "not set up"),
        "UNKNOWN": ("unknown", "unknown"),
        "BROKEN": ("attention", "needs attention"),
    }.get(verdict.upper(), ("unknown", "unknown"))


def _render_health(data: dict[str, Any]) -> str:
    health = _mapping(data.get("health"))
    status = str(health.get("status") or "unknown")
    if status != "fresh":
        return (
            '<p class="quiet health-guidance">'
            "Run /dex-doctor for a fresh checkup."
            "</p>"
        )
    rows = []
    for check in _list(health.get("checks")):
        if not isinstance(check, dict):
            continue
        css, label = _health_label(str(check.get("verdict") or "UNKNOWN"))
        feature = str(check.get("feature") or check.get("id") or "Unknown check")
        rows.append(
            '<li>'
            f'<span class="dot dot-{css}" aria-hidden="true"></span>'
            f'<span class="health-name">{_escape(feature)}</span>'
            f'<span class="health-state">{label}</span>'
            "</li>"
        )
    if not rows:
        return '<p class="quiet">The cached checkup has no individual checks to show.</p>'
    return f'<ul class="health-list">{"".join(rows)}</ul>'


def _render_state(data: dict[str, Any]) -> str:
    return f"""
    <section id="state" aria-labelledby="state-heading">
      <div class="section-heading">
        <p class="kicker">Configuration</p>
        <h2 id="state-heading">State of your Dex</h2>
      </div>
      <div class="state-grid">
        <div class="state-panel">
          <h3>You</h3>
          {_render_profile_state(data)}
        </div>
        <div class="state-panel">
          <h3>Integrations</h3>
          {_render_integrations(data)}
        </div>
      </div>
      <div class="health-panel">
        <div>
          <h3>Latest Dex checkup</h3>
          <p class="health-note">This reflects the last saved /dex-doctor check, not a new scan.</p>
        </div>
        {_render_health(data)}
      </div>
    </section>"""


def _display_date(data: dict[str, Any]) -> str:
    raw = _mapping(data.get("meta")).get("generated_at")
    if not isinstance(raw, str):
        return "Date unavailable"
    try:
        generated = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return "Date unavailable"
    return f"{generated:%A, %B} {generated.day}, {generated.year}"


def render_dashboard_html(
    data: dict[str, Any],
    observations: dict[str, Any] | None = None,
    *,
    archive_count: int = 0,
    archived: bool = False,
    journey: dict[str, Any] | None = None,
    history_data: dict[str, Any] | None = None,
    server_ctx: dict[str, Any] | None = None,
) -> str:
    """Render escaped data into one self-contained Nightfall HTML document."""
    observations = observations or {}
    profile = _mapping(data.get("profile"))
    name = str(profile.get("name") or "").strip()
    identity = f"<p class=\"user-name\">{_escape(name)}</p>" if name else ""
    archive_note = f"snapshot #{archive_count} saved" if archived else "snapshot not saved"
    suggestion = _render_suggestion(observations)
    journey_section = render_journey(journey) if journey is not None else ""
    history_section = render_history(history_data) if history_data is not None else ""
    server_meta = ""
    settings_section = ""
    settings_script = ""
    if server_ctx:
        settings_section, settings_script = render_settings(data, server_ctx)
        server_meta = (
            '\n  <meta name="dashboard-port" '
            f'content="{_escape(server_ctx.get("port", ""))}">'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">{server_meta}
  <title>Your Dex</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0B0F14;
      --surface: #11171e;
      --surface-raised: #151d25;
      --text: #edf4f5;
      --muted: #91a0a5;
      --faint: #66747a;
      --border: rgba(255, 255, 255, .08);
      --accent: #62d7d1;
      --accent-soft: rgba(98, 215, 209, .11);
      --good: #74d5a5;
      --attention: #e7c978;
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--bg); }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, "SF Pro", "Segoe UI", sans-serif;
      font-size: 16px;
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
    }}
    main {{ max-width: 880px; margin: 0 auto; padding: clamp(2rem, 7vw, 6rem) 1.25rem 2.5rem; }}
    header {{ padding: 0 0 clamp(3.5rem, 9vw, 7rem); }}
    .kicker {{
      margin: 0 0 .7rem;
      color: var(--accent);
      font-size: .76rem;
      font-weight: 700;
      letter-spacing: .1em;
      text-transform: uppercase;
    }}
    h1, h2, h3, p {{ overflow-wrap: anywhere; }}
    h1 {{ margin: 0; font-size: clamp(3rem, 10vw, 6.8rem); line-height: .94; letter-spacing: -.065em; font-weight: 720; }}
    .user-name {{ margin: 1rem 0 0; color: var(--text); font-size: clamp(1.25rem, 3vw, 1.7rem); }}
    .generated-date {{ margin: .2rem 0 0; color: var(--muted); }}
    section {{ padding: clamp(2.1rem, 6vw, 4rem) 0; border-top: 1px solid var(--border); }}
    .section-heading {{ max-width: 38rem; margin-bottom: 2rem; }}
    h2 {{ margin: 0; font-size: clamp(1.75rem, 5vw, 2.8rem); line-height: 1.08; letter-spacing: -.035em; }}
    h3 {{ margin: 0 0 .7rem; font-size: .82rem; color: var(--muted); letter-spacing: .04em; text-transform: uppercase; }}
    .quiet, .health-note {{ color: var(--muted); }}
    .receipt-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 2.5rem; }}
    .receipt-group p {{ margin: .28rem 0; font-size: clamp(1.08rem, 2vw, 1.28rem); }}
    .prose {{ max-width: 44rem; }}
    .observation {{ margin: 0 0 1.5rem; color: #dbe5e7; font-size: clamp(1.05rem, 2.2vw, 1.22rem); }}
    .observation p {{ margin: 0; }}
    strong {{ color: var(--text); font-weight: 720; }}
    code {{
      padding: .12em .35em;
      border: 1px solid var(--border);
      border-radius: .3rem;
      background: rgba(255, 255, 255, .04);
      color: #c8f2ef;
      font: .9em/1.4 ui-monospace, "SFMono-Regular", Consolas, monospace;
    }}
    a {{ color: var(--accent); text-underline-offset: .2em; }}
    .suggestion {{
      margin: 2rem 0;
      padding: clamp(1.5rem, 5vw, 2.6rem);
      border: 1px solid rgba(98, 215, 209, .24);
      border-radius: 1rem;
      background: linear-gradient(145deg, var(--accent-soft), rgba(255, 255, 255, .015) 60%);
    }}
    .suggestion-why {{ max-width: 42rem; margin: .8rem 0 0; color: var(--muted); }}
    .try-block {{ position: relative; margin-top: 1.8rem; padding: 1.1rem; border: 1px solid var(--border); border-radius: .7rem; background: rgba(4, 8, 12, .56); }}
    .try-label {{ margin-bottom: .6rem; color: var(--accent); font-size: .78rem; font-weight: 700; }}
    pre {{ margin: 0; padding-right: 7rem; white-space: pre-wrap; overflow-wrap: anywhere; }}
    pre code {{ padding: 0; border: 0; background: transparent; color: var(--text); }}
    button {{
      position: absolute;
      top: .8rem;
      right: .8rem;
      border: 1px solid rgba(98, 215, 209, .35);
      border-radius: .5rem;
      background: rgba(98, 215, 209, .12);
      color: var(--text);
      padding: .48rem .72rem;
      font: inherit;
      font-size: .82rem;
      cursor: pointer;
    }}
    button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 3px; }}
    .copy-status {{ position: absolute; right: .9rem; bottom: .45rem; color: var(--muted); font-size: .72rem; }}
    .state-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 1rem; }}
    .state-panel, .health-panel {{ padding: 1.25rem; border: 1px solid var(--border); border-radius: .8rem; background: var(--surface); }}
    .state-list {{ margin: 0; }}
    .state-list div {{ padding: .65rem 0; border-bottom: 1px solid var(--border); }}
    .state-list div:last-child {{ border-bottom: 0; }}
    dt {{ color: var(--faint); font-size: .75rem; }}
    dd {{ margin: .12rem 0 0; }}
    .integration-list, .health-list {{ list-style: none; margin: 0; padding: 0; }}
    .integration-list li, .health-list li {{ display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: .65rem; padding: .48rem 0; }}
    .integration-state, .health-state {{ color: var(--muted); font-size: .84rem; }}
    .dot {{ width: .48rem; height: .48rem; border-radius: 50%; background: var(--faint); }}
    .dot-on, .dot-good {{ background: var(--good); box-shadow: 0 0 0 3px rgba(116, 213, 165, .08); }}
    .dot-attention {{ background: var(--attention); }}
    .dot-off, .dot-unknown {{ background: var(--faint); }}
    .health-panel {{ display: grid; grid-template-columns: minmax(12rem, .8fr) minmax(16rem, 1.2fr); gap: 1.5rem; margin-top: 1rem; background: var(--surface-raised); }}
    .health-note {{ margin: 0; font-size: .82rem; }}
    .health-guidance {{ margin: 0; }}
    .journey-grid {{ align-items: start; }}
    .territory {{ min-width: 0; }}
    .journey-chips {{
      display: flex;
      flex-wrap: wrap;
      gap: .55rem;
      list-style: none;
      margin: 0;
      padding: 0;
    }}
    .journey-chip {{
      padding: .34rem .58rem;
      border: 1px solid var(--border);
      border-radius: .55rem;
      font-size: .78rem;
      line-height: 1.35;
    }}
    .journey-chip.lit {{
      border-color: rgba(98, 215, 209, .42);
      background: var(--accent-soft);
      color: var(--text);
    }}
    .journey-chip.dim {{
      border-color: rgba(255, 255, 255, .06);
      color: var(--faint);
    }}
    .journey-chip.outlined {{
      border-style: dashed;
      border-color: rgba(145, 160, 165, .28);
      color: var(--faint);
    }}
    .history-trends {{ gap: 1rem; margin-bottom: 1rem; }}
    .history-chart svg {{ display: block; width: 100%; height: auto; }}
    .history-milestone {{ margin: 1rem 0; padding: 1rem 1.25rem; }}
    .history-milestone h3 {{ margin: 0; color: var(--text); }}
    .history-looking-back {{ margin-top: 1rem; }}
    .history-looking-back p {{ margin: 0; }}
    .settings-list {{
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: .8rem;
      background: var(--surface);
    }}
    .setting-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 1.25rem;
      padding: 1rem 1.15rem;
      border-bottom: 1px solid var(--border);
    }}
    .setting-row:last-child {{ border-bottom: 0; }}
    .setting-row-readonly {{ grid-template-columns: 1fr; }}
    .setting-copy {{ min-width: 0; }}
    .setting-copy label, .setting-label {{
      display: block;
      color: var(--text);
      font-size: .94rem;
      font-weight: 650;
    }}
    .setting-copy p {{ margin: .18rem 0 0; color: var(--muted); font-size: .78rem; }}
    .setting-action {{
      display: flex;
      min-width: 8.5rem;
      flex-direction: column;
      align-items: flex-end;
      gap: .25rem;
    }}
    .setting-action input[role="switch"] {{
      appearance: none;
      position: relative;
      width: 2.6rem;
      height: 1.45rem;
      margin: 0;
      border: 1px solid rgba(145, 160, 165, .3);
      border-radius: 999px;
      background: rgba(255, 255, 255, .06);
      cursor: pointer;
      transition: background .16s ease, border-color .16s ease;
    }}
    .setting-action input[role="switch"]::after {{
      content: "";
      position: absolute;
      top: .18rem;
      left: .2rem;
      width: .96rem;
      height: .96rem;
      border-radius: 50%;
      background: var(--muted);
      transition: transform .16s ease, background .16s ease;
    }}
    .setting-action input[role="switch"]:checked {{
      border-color: rgba(98, 215, 209, .62);
      background: rgba(98, 215, 209, .32);
    }}
    .setting-action input[role="switch"]:checked::after {{
      background: var(--accent);
      transform: translateX(1.16rem);
    }}
    .setting-action input[role="switch"]:focus-visible,
    .setting-action select:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 3px;
    }}
    .setting-action input:disabled, .setting-action select:disabled {{
      cursor: not-allowed;
      opacity: .48;
    }}
    .setting-action select {{
      min-width: 10.5rem;
      max-width: 15rem;
      border: 1px solid rgba(145, 160, 165, .26);
      border-radius: .55rem;
      background: var(--surface-raised);
      color: var(--text);
      padding: .48rem 1.8rem .48rem .62rem;
      font: inherit;
      font-size: .82rem;
    }}
    .setting-status {{
      min-height: 1.1rem;
      color: var(--muted);
      font-size: .7rem;
      text-align: right;
    }}
    .settings-subsection {{ margin-top: 2rem; }}
    .settings-subsection h3 {{ margin-bottom: .8rem; }}
    .handoff-button {{
      position: static;
      display: flex;
      width: 100%;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      border-color: var(--border);
      background: var(--surface);
      padding: .85rem 1rem;
      text-align: left;
    }}
    .handoff-button:hover {{ border-color: rgba(98, 215, 209, .28); }}
    .handoff-button span {{ color: var(--muted); font-size: .75rem; font-weight: 400; }}
    .handoff-status {{ display: block; min-height: 1.2rem; margin-top: .45rem; color: var(--muted); font-size: .75rem; }}
    .undo-button {{
      position: static;
      margin: 0 0 0 .15rem;
      border: 0;
      background: transparent;
      color: var(--accent);
      padding: 0;
      font-size: inherit;
      text-decoration: underline;
      text-underline-offset: .15em;
    }}
    footer {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: .5rem 1rem; padding-top: 2rem; border-top: 1px solid var(--border); color: var(--faint); font-size: .78rem; }}
    @media (max-width: 600px) {{
      main {{ padding-inline: 1rem; }}
      .health-panel {{ grid-template-columns: 1fr; }}
      .setting-row {{ grid-template-columns: 1fr; }}
      .setting-action {{ min-width: 0; align-items: flex-start; }}
      .setting-status {{ text-align: left; }}
      .handoff-button {{ align-items: flex-start; flex-direction: column; }}
      pre {{ padding: 2.8rem 0 0; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <p class="kicker">Local overview</p>
      <h1>Your Dex</h1>
      {identity}
      <p class="generated-date">{_escape(_display_date(data))}</p>
    </header>
    {_render_receipt(data)}
    {_render_observations(observations)}
    {suggestion}
    {journey_section}
    {_render_state(data)}
    {history_section}
    {settings_section}
    <footer>
      <span>Generated locally by Dex · nothing leaves your machine</span>
      <span>{_escape(archive_note)}</span>
    </footer>
  </main>
  <script>
    (() => {{
      const button = document.getElementById('copyPrompt');
      const prompt = document.getElementById('tryPrompt');
      const status = document.getElementById('copyStatus');
      if (!button || !prompt) return;
      const markCopied = () => {{
        if (status) status.textContent = 'Copied';
        window.setTimeout(() => {{ if (status) status.textContent = ''; }}, 1600);
      }};
      button.addEventListener('click', async () => {{
        const text = prompt.textContent || '';
        try {{
          if (!navigator.clipboard) throw new Error('clipboard unavailable');
          await navigator.clipboard.writeText(text);
          markCopied();
        }} catch (_) {{
          const range = document.createRange();
          range.selectNodeContents(prompt);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          document.execCommand('copy');
          selection.removeAllRanges();
          markCopied();
        }}
      }});
    }})();
    {settings_script}
  </script>
</body>
</html>
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _history_path(vault: Path) -> Path:
    return vault / DEX_RUNTIME_DIR.relative_to(VAULT_ROOT) / "dashboard" / "history.jsonl"


def _history_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(bool(line.strip()) for line in path.read_text(encoding="utf-8", errors="replace").splitlines())


def _snapshot(data: dict[str, Any], observations: dict[str, Any], now: datetime) -> dict[str, Any]:
    integrations = _mapping(data.get("integrations"))
    integrations_on = _number(integrations, "enabled_count")
    if not integrations_on:
        integrations_on = sum(
            bool(_mapping(app).get("enabled"))
            for app in _mapping(integrations.get("apps")).values()
        )
    return {
        "ts": _timestamp(now),
        "counts": {
            "tasks_done": _number(data.get("tasks"), "completed"),
            "people": _number(data.get("people"), "total"),
            "meetings": _number(data.get("meetings"), "total"),
            "skills_used": len(
                [
                    name
                    for name in _list(_mapping(data.get("skills")).get("used"))
                    if isinstance(name, str)
                ]
            ),
            "integrations_on": integrations_on,
        },
        "observations": _observation_strings(observations),
        "suggestion_title": _suggestion(observations)["title"],
    }


def _history_section_data(
    vault: Path,
    data: dict[str, Any],
    observations: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        entries = dashboard_history.load_history(vault)
        if not entries:
            return None
        previous_counts = _mapping(entries[-2].get("counts")) if len(entries) > 1 else {}
        new_counts = _mapping(entries[-1].get("counts"))
        raw_vault_age = _mapping(_mapping(data.get("meta")).get("vault_age")).get("age_days")
        vault_age = (
            raw_vault_age
            if isinstance(raw_vault_age, int)
            and not isinstance(raw_vault_age, bool)
            and raw_vault_age >= 0
            else None
        )
        trend_input = {
            "analytics": _mapping(data.get("analytics")),
            "history": entries,
        }
        return {
            "history": entries,
            "trends": dashboard_history.weekly_trends(trend_input),
            "milestones": dashboard_history.detect_milestones(
                previous_counts,
                new_counts,
                vault_age,
            ),
            "looking_back": observations.get("looking_back"),
        }
    except Exception:
        return None


def render_dashboard(
    vault: Path | str,
    data: dict[str, Any],
    observations: dict[str, Any] | None,
    output: Path | str,
    *,
    archive: bool = True,
    now: datetime | None = None,
    server_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the page and, unless disabled, one compact local history line."""
    vault_path = Path(vault).expanduser().resolve()
    output_path = Path(output).expanduser()
    observation_data = observations or {}
    generated = now or _utc_now()
    history = _history_path(vault_path)
    archive_count = 0
    if archive:
        archive_count = _history_count(history) + 1
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    _snapshot(data, observation_data, generated),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    try:
        journey_data = dashboard_journey.build_journey(vault_path, data)
    except Exception:
        journey_data = None
    history_data = _history_section_data(vault_path, data, observation_data)
    page = render_dashboard_html(
        data,
        observation_data,
        archive_count=archive_count,
        archived=archive,
        journey=journey_data,
        history_data=history_data,
        server_ctx=server_ctx,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")
    return {
        "output": str(output_path),
        "archived": archive,
        "archive_count": archive_count,
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True, help="Dex vault root")
    parser.add_argument("--data", type=Path, required=True, help="Collected dashboard JSON")
    parser.add_argument("--observations", type=Path, help="Authored observations JSON")
    parser.add_argument("--out", type=Path, required=True, help="HTML output path")
    parser.add_argument("--no-archive", action="store_true", help="Do not append a history snapshot")
    parser.add_argument(
        "--with-settings",
        action="store_true",
        help="Include server-ready local settings controls",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.vault.expanduser().is_dir():
        print(f"Error: vault is not a directory: {args.vault}", file=sys.stderr)
        return 2
    try:
        data = _load_json_object(args.data)
        observations = _load_json_object(args.observations) if args.observations else {}
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Error: could not read dashboard input: {str(error).splitlines()[0]}", file=sys.stderr)
        return 2
    try:
        result = render_dashboard(
            args.vault,
            data,
            observations,
            args.out,
            archive=not args.no_archive,
            server_ctx=(
                {"token": TOKEN_PLACEHOLDER, "port": PORT_PLACEHOLDER}
                if args.with_settings
                else None
            ),
        )
    except OSError as error:
        print(f"Error: could not write dashboard output: {str(error).splitlines()[0]}", file=sys.stderr)
        return 1
    print(result["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
