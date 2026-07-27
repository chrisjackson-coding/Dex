"""Vetted, conflict-aware writes for the local Dex Dashboard."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.paths import (
    DEX_RUNTIME_DIR,
    INTEGRATION_CONFIG_FILE,
    SYSTEM_DIR,
    USER_PROFILE_FILE,
    VAULT_ROOT,
)

FORMALITY_VALUES = ("formal", "professional_casual", "casual")
DIRECTNESS_VALUES = ("very_direct", "balanced", "supportive")
ENTITY_CREATION_VALUES = ("auto", "suggest", "off")
HEALTH_TELEMETRY_VALUES = ("opted-in", "opted-out")
HEALTH_TELEMETRY_STORED_VALUES = ("pending", *HEALTH_TELEMETRY_VALUES)
INTEGRATION_SETTING = re.compile(r"^integration:(?P<name>[A-Za-z0-9][A-Za-z0-9_-]*)\.enabled$")
YAML_LINE = re.compile(
    r"^(?P<prefix>(?P<indent> *)"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*):[ \t]*)"
    r"(?P<value>[^\r\n]*)(?P<newline>\r?\n?)$"
)
HEALTH_LINE = re.compile(
    r"^(?P<prefix>\*\*Health telemetry:\*\*[ \t]*)"
    r"(?P<value>[^\r\n]*?)(?P<suffix>[ \t]*)(?P<newline>\r?\n?)$",
    re.MULTILINE,
)
BLOCK_SCALAR = re.compile(r"^[|>](?:[1-9][+-]?|[+-][1-9]?|[+-])?$")


class ToggleError(Exception):
    """Base class for safe, user-presentable toggle failures."""

    status_code = 500


class ToggleValidationError(ToggleError):
    """The requested setting or value is outside the vetted registry."""

    status_code = 400


class ToggleConflictError(ToggleError):
    """The source changed after the page last read it."""

    status_code = 409


class ToggleSchemaError(ToggleConflictError):
    """A target file is not safe for exact, single-line surgery."""


@dataclass(frozen=True)
class ToggleSpec:
    file_kind: str
    yaml_path: tuple[str, ...] | None
    value_kind: str
    allowed: tuple[Any, ...]


TOGGLE_REGISTRY = {
    "analytics_enabled": ToggleSpec(
        "profile",
        ("analytics", "enabled"),
        "bool",
        (False, True),
    ),
    "entity_creation": ToggleSpec(
        "profile",
        ("entity_creation", "mode"),
        "enum",
        ENTITY_CREATION_VALUES,
    ),
    "formality": ToggleSpec(
        "profile",
        ("communication", "formality"),
        "enum",
        FORMALITY_VALUES,
    ),
    "directness": ToggleSpec(
        "profile",
        ("communication", "directness"),
        "enum",
        DIRECTNESS_VALUES,
    ),
    "health_telemetry": ToggleSpec(
        "usage",
        None,
        "enum",
        HEALTH_TELEMETRY_VALUES,
    ),
}

PROFILE_SCHEMA = {setting_id: spec for setting_id, spec in TOGGLE_REGISTRY.items() if spec.file_kind == "profile"}


@dataclass(frozen=True)
class FileStamp:
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class StateSnapshot:
    values: dict[str, Any]
    stamps: dict[str, FileStamp]


@dataclass(frozen=True)
class WriteResult:
    setting_id: str
    old: Any
    new: Any
    stamp: FileStamp


@dataclass(frozen=True)
class _YamlEntry:
    line_index: int
    path: tuple[str, ...]
    indent: int
    prefix: str
    scalar: str
    suffix: str
    newline: str


def _at(vault: Path, configured_path: Path) -> Path:
    """Rebase a core.paths constant from its configured vault onto this invocation."""
    return vault / configured_path.relative_to(VAULT_ROOT)


def _usage_log_file(vault: Path) -> Path:
    return _at(vault, SYSTEM_DIR / "usage_log.md")


def _audit_file(vault: Path) -> Path:
    return _at(vault, DEX_RUNTIME_DIR / "dashboard" / "audit.jsonl")


def _split_scalar_suffix(value: str) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
            continue
        if character == "#" and (index == 0 or value[index - 1].isspace()):
            before = value[:index]
            scalar = before.rstrip()
            return scalar, before[len(scalar) :] + value[index:]
    return value.rstrip(), value[len(value.rstrip()) :]


def _yaml_entries(text: str) -> list[_YamlEntry]:
    entries: list[_YamlEntry] = []
    parents: list[tuple[int, tuple[str, ...]]] = []
    block_scalar_indent: int | None = None
    for line_index, line in enumerate(text.splitlines(keepends=True)):
        if block_scalar_indent is not None:
            if not line.strip():
                continue
            line_indent = len(line) - len(line.lstrip(" "))
            if line_indent > block_scalar_indent:
                continue
            block_scalar_indent = None
        match = YAML_LINE.match(line)
        if match is None:
            continue
        indent = len(match.group("indent"))
        while parents and parents[-1][0] >= indent:
            parents.pop()
        parent = parents[-1][1] if parents else ()
        path = (*parent, match.group("key"))
        scalar, suffix = _split_scalar_suffix(match.group("value"))
        entry = _YamlEntry(
            line_index=line_index,
            path=path,
            indent=indent,
            prefix=match.group("prefix"),
            scalar=scalar,
            suffix=suffix,
            newline=match.group("newline"),
        )
        entries.append(entry)
        if BLOCK_SCALAR.fullmatch(scalar.strip()):
            block_scalar_indent = indent
        elif not scalar:
            parents.append((indent, path))
    return entries


def _parse_scalar(scalar: str) -> Any:
    value = scalar.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ToggleSchemaError("A quoted setting value is malformed; refresh after fixing it.") from error
        if not isinstance(parsed, str):
            raise ToggleSchemaError("A quoted setting value has the wrong type; refresh after fixing it.")
        return parsed
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if value == "true":
        return True
    if value == "false":
        return False
    if value in {"null", "~"}:
        return None
    return value


def _render_scalar(value: Any, old_scalar: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if old_scalar.startswith('"') and old_scalar.endswith('"'):
        return json.dumps(value, ensure_ascii=False)
    if old_scalar.startswith("'") and old_scalar.endswith("'"):
        return "'" + str(value).replace("'", "''") + "'"
    return str(value)


def _entry_map(entries: list[_YamlEntry]) -> dict[tuple[str, ...], list[_YamlEntry]]:
    result: dict[tuple[str, ...], list[_YamlEntry]] = {}
    for entry in entries:
        result.setdefault(entry.path, []).append(entry)
    return result


def _require_one(
    by_path: dict[tuple[str, ...], list[_YamlEntry]],
    path: tuple[str, ...],
    label: str,
) -> _YamlEntry:
    matches = by_path.get(path, [])
    if len(matches) != 1:
        raise ToggleSchemaError(
            f"{label} must appear exactly once before Dex can change it; refresh after fixing the file."
        )
    return matches[0]


def _value_is_allowed(value: Any, spec: ToggleSpec) -> bool:
    if spec.value_kind == "bool":
        return isinstance(value, bool)
    return isinstance(value, str) and value in spec.allowed


def _validate_requested_value(setting_id: str, value: Any, spec: ToggleSpec) -> None:
    if not _value_is_allowed(value, spec):
        choices = ", ".join(str(choice).lower() for choice in spec.allowed)
        raise ToggleValidationError(f"{setting_id} accepts only: {choices}.")


def _profile_values(text: str) -> tuple[dict[str, Any], dict[str, _YamlEntry]]:
    entries = _yaml_entries(text)
    by_path = _entry_map(entries)
    values: dict[str, Any] = {}
    anchors: dict[str, _YamlEntry] = {}
    for setting_id, spec in PROFILE_SCHEMA.items():
        assert spec.yaml_path is not None
        entry = _require_one(by_path, spec.yaml_path, setting_id)
        value = _parse_scalar(entry.scalar)
        if not _value_is_allowed(value, spec):
            raise ToggleSchemaError(
                f"{setting_id} has a value outside the supported schema; refresh after fixing the file."
            )
        values[setting_id] = value
        anchors[setting_id] = entry
    _reject_duplicate_yaml_paths(entries)
    return values, anchors


def _reject_duplicate_yaml_paths(entries: list[_YamlEntry]) -> None:
    duplicates = [path for path, count in Counter(entry.path for entry in entries).items() if count > 1]
    if duplicates:
        label = ".".join(duplicates[0])
        raise ToggleSchemaError(
            f"{label} must appear exactly once before Dex can change settings; refresh after fixing the file."
        )


def _integration_values(text: str) -> tuple[dict[str, bool], dict[str, _YamlEntry]]:
    entries = _yaml_entries(text)
    _reject_duplicate_yaml_paths(entries)
    candidates: dict[str, list[_YamlEntry]] = {}
    for entry in entries:
        name: str | None = None
        if len(entry.path) == 2 and entry.path[0] == "enabled":
            name = entry.path[1]
        elif (
            len(entry.path) == 2
            and entry.path[1] == "enabled"
            and entry.path[0] not in {"enabled", "hooks", "detected"}
        ):
            name = entry.path[0]
        if name is None or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) is None:
            continue
        candidates.setdefault(name, []).append(entry)

    values: dict[str, bool] = {}
    anchors: dict[str, _YamlEntry] = {}
    for name, matches in sorted(candidates.items()):
        if len(matches) != 1:
            raise ToggleSchemaError(
                f"integration {name} must have exactly one enabled setting; refresh after fixing the file."
            )
        value = _parse_scalar(matches[0].scalar)
        if not isinstance(value, bool):
            raise ToggleSchemaError(f"integration {name} enabled must be true or false; refresh after fixing the file.")
        setting_id = f"integration:{name}.enabled"
        values[setting_id] = value
        anchors[setting_id] = matches[0]
    return values, anchors


def _health_value(text: str) -> tuple[str, re.Match[str]]:
    matches = list(HEALTH_LINE.finditer(text))
    if len(matches) != 1:
        raise ToggleSchemaError(
            "health_telemetry must appear exactly once before Dex can change it; refresh after fixing the file."
        )
    value = matches[0].group("value").strip()
    if value not in HEALTH_TELEMETRY_STORED_VALUES:
        raise ToggleSchemaError(
            "health_telemetry has a value outside the supported schema; refresh after fixing the file."
        )
    return value, matches[0]


def _replace_yaml_entry(text: str, entry: _YamlEntry, value: Any) -> str:
    lines = text.splitlines(keepends=True)
    lines[entry.line_index] = entry.prefix + _render_scalar(value, entry.scalar) + entry.suffix + entry.newline
    return "".join(lines)


def _same_yaml_keys(before: str, after: str) -> bool:
    return Counter(entry.path for entry in _yaml_entries(before)) == Counter(
        entry.path for entry in _yaml_entries(after)
    )


def _stamp(path: Path) -> FileStamp:
    data = path.read_bytes()
    file_stat = path.stat()
    return FileStamp(
        mtime_ns=file_stat.st_mtime_ns,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _read_stamped(path: Path) -> tuple[str, FileStamp]:
    for _attempt in range(2):
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if before.st_mtime_ns == after.st_mtime_ns and before.st_size == after.st_size:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ToggleSchemaError(
                    "A dashboard settings file is not valid UTF-8; refresh after fixing it."
                ) from error
            return text, FileStamp(
                mtime_ns=after.st_mtime_ns,
                sha256=hashlib.sha256(data).hexdigest(),
            )
    raise ToggleConflictError("The settings changed while Dex read them; refresh and try again.")


def atomic_replace_bytes(
    path: Path,
    content: bytes,
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    """Fsync a same-directory temporary file, then atomically replace the target."""
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ToggleEngine:
    """Read and mutate only the settings named by ``TOGGLE_REGISTRY``."""

    def __init__(self, vault: Path | str) -> None:
        self.vault = Path(vault).expanduser().resolve()

    def read_state(self) -> StateSnapshot:
        values: dict[str, Any] = {}
        stamps: dict[str, FileStamp] = {}

        profile_path = _at(self.vault, USER_PROFILE_FILE)
        if profile_path.is_file():
            profile_text, profile_stamp = _read_stamped(profile_path)
            profile_values, _anchors = _profile_values(profile_text)
            values.update(profile_values)
            stamps.update({setting_id: profile_stamp for setting_id in profile_values})

        usage_path = _usage_log_file(self.vault)
        if usage_path.is_file():
            usage_text, usage_stamp = _read_stamped(usage_path)
            health_value, _match = _health_value(usage_text)
            values["health_telemetry"] = health_value
            stamps["health_telemetry"] = usage_stamp

        integrations_path = _at(self.vault, INTEGRATION_CONFIG_FILE)
        if integrations_path.is_file():
            integrations_text, integrations_stamp = _read_stamped(integrations_path)
            integration_values, _anchors = _integration_values(integrations_text)
            values.update(integration_values)
            stamps.update({setting_id: integrations_stamp for setting_id in integration_values})

        return StateSnapshot(
            values=dict(sorted(values.items())),
            stamps={setting_id: stamps[setting_id] for setting_id in sorted(stamps)},
        )

    def write(
        self,
        setting_id: str,
        value: Any,
        *,
        expected: FileStamp | None,
    ) -> WriteResult:
        spec = TOGGLE_REGISTRY.get(setting_id)
        integration_match = INTEGRATION_SETTING.fullmatch(setting_id)
        if spec is None and integration_match is None:
            raise ToggleValidationError("That setting is not available from the dashboard.")

        if spec is not None:
            _validate_requested_value(setting_id, value, spec)
            path = self._path_for_kind(spec.file_kind)
        else:
            if not isinstance(value, bool):
                raise ToggleValidationError(f"{setting_id} accepts only: false, true.")
            path = _at(self.vault, INTEGRATION_CONFIG_FILE)

        if not path.is_file():
            raise ToggleValidationError("That setting is not available from the dashboard.")

        current_text, current_stamp = _read_stamped(path)
        old, changed_text = self._change_text(
            setting_id,
            value,
            current_text,
            spec,
            integration_match,
        )
        if expected is None or current_stamp != expected:
            raise ToggleConflictError("The settings changed; refresh the dashboard and try again.")
        if changed_text == current_text:
            self._append_audit(setting_id, old, value)
            return WriteResult(setting_id, old, value, current_stamp)

        def confirm_unchanged() -> None:
            if _stamp(path) != current_stamp:
                raise ToggleConflictError("The settings changed; refresh the dashboard and try again.")

        atomic_replace_bytes(
            path,
            changed_text.encode("utf-8"),
            before_replace=confirm_unchanged,
        )
        new_stamp = _stamp(path)
        self._append_audit(setting_id, old, value)
        return WriteResult(setting_id, old, value, new_stamp)

    def _path_for_kind(self, file_kind: str) -> Path:
        if file_kind == "profile":
            return _at(self.vault, USER_PROFILE_FILE)
        if file_kind == "usage":
            return _usage_log_file(self.vault)
        raise ToggleValidationError("That setting is not available from the dashboard.")

    def _change_text(
        self,
        setting_id: str,
        value: Any,
        current_text: str,
        spec: ToggleSpec | None,
        integration_match: re.Match[str] | None,
    ) -> tuple[Any, str]:
        if spec is not None and spec.file_kind == "profile":
            values, anchors = _profile_values(current_text)
            changed = _replace_yaml_entry(current_text, anchors[setting_id], value)
            _profile_values(changed)
            if not _same_yaml_keys(current_text, changed):
                raise ToggleSchemaError("The profile shape changed unexpectedly. Refresh and try again.")
            return values[setting_id], changed

        if spec is not None and spec.file_kind == "usage":
            old, match = _health_value(current_text)
            changed = (
                current_text[: match.start()]
                + match.group("prefix")
                + str(value)
                + match.group("suffix")
                + match.group("newline")
                + current_text[match.end() :]
            )
            _health_value(changed)
            return old, changed

        assert integration_match is not None
        values, anchors = _integration_values(current_text)
        if setting_id not in anchors:
            raise ToggleValidationError("That integration is not already present in the config.")
        changed = _replace_yaml_entry(current_text, anchors[setting_id], value)
        _integration_values(changed)
        if not _same_yaml_keys(current_text, changed):
            raise ToggleSchemaError("The integration config shape changed unexpectedly. Refresh and try again.")
        return values[setting_id], changed

    def _append_audit(self, setting_id: str, old: Any, new: Any) -> None:
        path = _audit_file(self.vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "setting_id": setting_id,
            "old": old,
            "new": new,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
