import json
import re

_MAC_RE = re.compile(
    r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$"
    r"|^[0-9A-Fa-f]{12}$"
)


def validate_mac(mac: str) -> bool:
    return bool(_MAC_RE.match(mac))


def normalize_mac(mac: str) -> str:
    clean = re.sub(r"[:\-]", "", mac).lower()
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2))


def load_devices(devices_file: str) -> list:
    try:
        with open(devices_file) as f:
            data = json.load(f)
        return data.get("devices", [])
    except FileNotFoundError:
        return []


def save_devices(devices_file: str, device_list: list) -> None:
    try:
        with open(devices_file) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data["devices"] = device_list
    with open(devices_file, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
