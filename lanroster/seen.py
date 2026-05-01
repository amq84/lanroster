"""Local (non-git) last-seen timestamps, keyed by MAC address."""

import json
from datetime import datetime, timezone
from pathlib import Path

_SEEN_FILE = Path.home() / ".lanroster" / "seen.json"


def _load() -> dict:
    try:
        with open(_SEEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    _SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_SEEN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def update_from_scan(scan_results: list[tuple[str, str]]) -> None:
    data = _load()
    now = datetime.now(timezone.utc).isoformat()
    for _, mac in scan_results:
        data[mac.lower()] = now
    _save(data)


def get_last_seen(mac: str) -> str | None:
    return _load().get(mac.lower())


def relative(ts: str) -> str:
    """Return a human-readable relative timestamp (e.g. '3h ago')."""
    try:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return "—"
