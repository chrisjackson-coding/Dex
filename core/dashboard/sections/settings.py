"""Render the interactive, server-backed Dashboard settings section."""

from __future__ import annotations

import html
import json
import re
from typing import Any

SAFE_INTEGRATION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _inline_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _switch(setting_id: str, label: str, explanation: str, *, value_kind: str = "bool") -> str:
    checked_values = ""
    if value_kind == "health":
        checked_values = ' data-checked-value="opted-in" data-unchecked-value="opted-out"'
    return f"""
      <div class="setting-row" data-setting-row>
        <div class="setting-copy">
          <label for="setting-{_escape(setting_id)}">{_escape(label)}</label>
          <p>{_escape(explanation)}</p>
        </div>
        <div class="setting-action">
          <input
            id="setting-{_escape(setting_id)}"
            type="checkbox"
            role="switch"
            data-setting-id="{_escape(setting_id)}"
            data-value-kind="{_escape(value_kind)}"
            {checked_values}
            disabled
          >
          <span class="setting-status" data-setting-status aria-live="polite"></span>
        </div>
      </div>"""


def _select(
    setting_id: str,
    label: str,
    explanation: str,
    options: tuple[tuple[str, str], ...],
) -> str:
    option_html = "".join(
        f'<option value="{_escape(value)}">{_escape(option_label)}</option>' for value, option_label in options
    )
    return f"""
      <div class="setting-row" data-setting-row>
        <div class="setting-copy">
          <label for="setting-{_escape(setting_id)}">{_escape(label)}</label>
          <p>{_escape(explanation)}</p>
        </div>
        <div class="setting-action">
          <select
            id="setting-{_escape(setting_id)}"
            data-setting-id="{_escape(setting_id)}"
            data-value-kind="enum"
            disabled
          >{option_html}</select>
          <span class="setting-status" data-setting-status aria-live="polite"></span>
        </div>
      </div>"""


def _integration_rows(data: dict[str, Any]) -> str:
    apps = _mapping(_mapping(data.get("integrations")).get("apps"))
    rows = []
    for raw_name in sorted(apps, key=lambda item: str(item).casefold()):
        name = str(raw_name)
        if SAFE_INTEGRATION_NAME.fullmatch(name):
            setting_id = f"integration:{name}.enabled"
            rows.append(
                _switch(
                    setting_id,
                    name.replace("_", " ").replace("-", " ").title(),
                    "Let this existing connection contribute context to Dex.",
                )
            )
        else:
            rows.append(
                f"""
      <div class="setting-row setting-row-readonly">
        <div class="setting-copy">
          <span class="setting-label">{_escape(name)}</span>
          <p>Manage this connection with Dex in conversation.</p>
        </div>
      </div>"""
            )
    if not rows:
        return '<p class="quiet">No existing integrations are available to switch here.</p>'
    return "".join(rows)


def render(
    data: dict[str, Any],
    server_ctx: dict[str, Any],
) -> tuple[str, str]:
    """Return the settings HTML fragment and its inline JavaScript."""
    token = str(server_ctx.get("token") or "")
    analytics_switch = _switch(
        "analytics_enabled",
        "Anonymous product analytics",
        "Share feature-use counts, never names, notes, or file contents.",
    )
    entity_select = _select(
        "entity_creation",
        "New people and companies",
        "Choose whether Dex creates pages, suggests them, or stays off.",
        (("auto", "Create automatically"), ("suggest", "Suggest first"), ("off", "Off")),
    )
    formality_select = _select(
        "formality",
        "Formality",
        "Set how polished or conversational Dex sounds.",
        (
            ("formal", "Formal"),
            ("professional_casual", "Professional, relaxed"),
            ("casual", "Casual"),
        ),
    )
    directness_select = _select(
        "directness",
        "Directness",
        "Set how directly Dex gives advice and feedback.",
        (
            ("very_direct", "Very direct"),
            ("balanced", "Balanced"),
            ("supportive", "Supportive"),
        ),
    )
    health_switch = _switch(
        "health_telemetry",
        "Anonymous health telemetry",
        "Share nightly pass/fail counts only; this is separate from analytics.",
        value_kind="health",
    )
    integration_rows = _integration_rows(data)
    fragment = f"""
    <section id="settings" aria-labelledby="settings-heading">
      <div class="section-heading">
        <p class="kicker">Settings</p>
        <h2 id="settings-heading">Tune Dex from here</h2>
        <p class="quiet">These changes stay in your local Dex files.</p>
      </div>
      <div class="settings-list">
        {analytics_switch}
        {entity_select}
        {formality_select}
        {directness_select}
        {health_switch}
      </div>
      <div class="settings-subsection">
        <h3>Existing integrations</h3>
        <div class="settings-list">{integration_rows}</div>
      </div>
      <div class="settings-subsection">
        <h3>Set up something new</h3>
        <button type="button" class="handoff-button" data-command="/todoist-setup">
          Set up Todoist
          <span>Dex walks you through it (run /todoist-setup)</span>
        </button>
        <span class="handoff-status" data-handoff-status aria-live="polite"></span>
      </div>
    </section>"""

    script = f"""
(() => {{
  const dashboardToken = {_inline_json(token)};
  const controls = Array.from(document.querySelectorAll('[data-setting-id]'));
  const currentValues = new Map();

  function apiUrl(path) {{
    const url = new URL(path, window.location.href);
    url.searchParams.set('t', dashboardToken);
    return url.toString();
  }}

  function statusFor(control) {{
    return control.closest('[data-setting-row]').querySelector('[data-setting-status]');
  }}

  function valueFrom(control) {{
    if (control.dataset.valueKind === 'bool') return control.checked;
    if (control.dataset.valueKind === 'health') {{
      return control.checked ? control.dataset.checkedValue : control.dataset.uncheckedValue;
    }}
    return control.value;
  }}

  function applyValue(control, value) {{
    if (control.dataset.valueKind === 'bool') control.checked = value === true;
    else if (control.dataset.valueKind === 'health') control.checked = value === 'opted-in';
    else control.value = value;
  }}

  async function readJson(response) {{
    const payload = await response.json().catch(() => ({{ error: 'Dex could not read the response.' }}));
    if (!response.ok) throw new Error(payload.error || 'Dex could not save that change.');
    return payload;
  }}

  async function loadState() {{
    try {{
      const response = await fetch(apiUrl('/api/state'), {{
        headers: {{ Accept: 'application/json' }},
        cache: 'no-store'
      }});
      const payload = await readJson(response);
      const unavailable = payload.unavailable || {{}};
      controls.forEach((control) => {{
        const settingId = control.dataset.settingId;
        if (Object.prototype.hasOwnProperty.call(payload.settings, settingId)) {{
          currentValues.set(settingId, payload.settings[settingId]);
          applyValue(control, payload.settings[settingId]);
          control.disabled = false;
          statusFor(control).textContent = '';
        }} else if (Object.prototype.hasOwnProperty.call(unavailable, settingId)) {{
          control.disabled = true;
          statusFor(control).textContent = unavailable[settingId];
        }} else {{
          control.disabled = true;
          statusFor(control).textContent = 'Not set up in this vault yet.';
        }}
      }});
    }} catch (error) {{
      controls.forEach((control) => {{
        control.disabled = true;
        statusFor(control).textContent = error.message;
      }});
    }}
  }}

  async function saveValue(control, nextValue, previousValue) {{
    const settingId = control.dataset.settingId;
    const status = statusFor(control);
    control.disabled = true;
    status.textContent = 'Saving…';
    try {{
      const response = await fetch(apiUrl('/api/toggle'), {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json', Accept: 'application/json' }},
        body: JSON.stringify({{ setting_id: settingId, value: nextValue }})
      }});
      const payload = await readJson(response);
      currentValues.set(settingId, payload.new);
      applyValue(control, payload.new);
      status.replaceChildren(document.createTextNode('Changed just now — '));
      const undo = document.createElement('button');
      undo.type = 'button';
      undo.className = 'undo-button';
      undo.textContent = 'undo';
      undo.addEventListener('click', () => {{
        const valueBeforeUndo = currentValues.get(settingId);
        applyValue(control, payload.old);
        currentValues.set(settingId, payload.old);
        saveValue(control, payload.old, valueBeforeUndo);
      }}, {{ once: true }});
      status.appendChild(undo);
    }} catch (error) {{
      currentValues.set(settingId, previousValue);
      applyValue(control, previousValue);
      status.textContent = error.message;
    }} finally {{
      control.disabled = false;
    }}
  }}

  controls.forEach((control) => {{
    control.addEventListener('change', () => {{
      const settingId = control.dataset.settingId;
      const previousValue = currentValues.get(settingId);
      const nextValue = valueFrom(control);
      currentValues.set(settingId, nextValue);
      saveValue(control, nextValue, previousValue);
    }});
  }});

  document.querySelectorAll('[data-command]').forEach((button) => {{
    button.addEventListener('click', async () => {{
      const command = button.dataset.command;
      const status = document.querySelector('[data-handoff-status]');
      try {{
        await navigator.clipboard.writeText(command);
        status.textContent = command + ' copied — paste it into Dex.';
      }} catch (_error) {{
        status.textContent = 'Run ' + command + ' in Dex.';
      }}
    }});
  }});

  let closeSent = false;
  function closeServer() {{
    if (closeSent) return;
    closeSent = true;
    const url = apiUrl('/api/close');
    if (navigator.sendBeacon) {{
      navigator.sendBeacon(url, new Blob(['{{}}'], {{ type: 'application/json' }}));
    }} else {{
      fetch(url, {{ method: 'POST', body: '{{}}', keepalive: true }}).catch(() => {{}});
    }}
  }}

  window.addEventListener('pagehide', closeServer);
  window.addEventListener('beforeunload', closeServer);
  loadState();
}})();
"""
    return fragment, script
