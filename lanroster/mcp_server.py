import asyncio
import json
import re
import time

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from . import config as cfg_mod
from . import devices as dev_mod
from . import git_ops
from . import network as net_mod
from . import seen as seen_mod
from . import vendor as vendor_mod

app = Server("lanroster")

_scan_cache: dict | None = None
_scan_time: float = 0.0
_CACHE_TTL: float = 60.0
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _get_config() -> dict:
    cfg = cfg_mod.get_config()
    if cfg is None:
        raise RuntimeError("Not initialized. Run 'lanroster init <repo_url>' first.")
    return cfg


def _text(data) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(data, indent=2))]


async def _get_status(force: bool = False) -> dict:
    global _scan_cache, _scan_time
    now = time.monotonic()
    if not force and _scan_cache and (now - _scan_time) < _CACHE_TTL:
        return _scan_cache
    cfg = _get_config()
    roster = dev_mod.load_devices(cfg["devices_file"])
    _, cidr = await asyncio.to_thread(net_mod.get_local_ip_and_network)
    result = await asyncio.to_thread(net_mod.scan_network, cidr)
    seen_mod.update_from_scan(result.hosts)
    network_map = {mac: ip for ip, mac in result.hosts}
    _scan_cache = {
        "roster": roster,
        "network_map": network_map,
        "method": result.method,
        "cidr": cidr,
    }
    _scan_time = now
    return _scan_cache


@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_devices",
            description="Return all registered devices from the roster.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_network_status",
            description=(
                "Scan the network and return the online/offline status of every registered "
                "device, including current IP addresses, vendors, and last-seen times. "
                "Use this to understand current infrastructure state before making decisions "
                "about reachability. Results are cached for 60 seconds; use force_refresh=true "
                "if you suspect cached data is stale."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Bypass the 60-second cache and force a fresh ARP scan.",
                        "default": False,
                    }
                },
            },
        ),
        types.Tool(
            name="get_device_ip",
            description=(
                "Return the current IP address of a named device. "
                "Use this before any SSH, rsync, or network operation targeting a named device."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The device name as registered in the roster.",
                    }
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="register_device",
            description=(
                "Register a new device in the shared roster and push to git. "
                "Always confirm with the user before calling this tool."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Device name — letters, digits, hyphens, underscores only.",
                    },
                    "mac": {
                        "type": "string",
                        "description": "MAC address in any standard format.",
                    },
                    "ssh_user": {
                        "type": "string",
                        "description": "Optional SSH username for this device (e.g. root, pi, abel).",
                    },
                },
                "required": ["name", "mac"],
            },
        ),
        types.Tool(
            name="remove_device",
            description=(
                "Remove a device from the roster and push to git. "
                "Always confirm with the user before calling this tool."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The device name to remove.",
                    }
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="find_unknown_devices",
            description=(
                "Scan the network and return devices present on the LAN but not registered "
                "in the roster. Useful for detecting new hardware or unexpected guests."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "list_devices":
            return await _tool_list_devices()
        elif name == "get_network_status":
            return await _tool_get_network_status(arguments.get("force_refresh", False))
        elif name == "get_device_ip":
            return await _tool_get_device_ip(arguments.get("name", ""))
        elif name == "register_device":
            return await _tool_register_device(
                arguments.get("name", ""), arguments.get("mac", ""),
                arguments.get("ssh_user"),
            )
        elif name == "remove_device":
            return await _tool_remove_device(arguments.get("name", ""))
        elif name == "find_unknown_devices":
            return await _tool_find_unknown_devices()
        else:
            return _text({"error": f"Unknown tool: {name}"})
    except Exception as exc:
        return _text({"error": str(exc)})


async def _tool_list_devices() -> list[types.TextContent]:
    cfg = _get_config()
    roster = dev_mod.load_devices(cfg["devices_file"])
    return _text(
        [
            {
                "name": d["name"],
                "mac": d["mac"],
                "ssh_user": d.get("ssh_user"),
                "vendor": vendor_mod.get_vendor(d["mac"]),
                "last_seen": seen_mod.get_last_seen(d["mac"]),
            }
            for d in roster
        ]
    )


async def _tool_get_network_status(force: bool) -> list[types.TextContent]:
    cfg = _get_config()
    await asyncio.to_thread(git_ops.pull_repo, cfg["repo_path"])
    state = await _get_status(force=force)
    roster = dev_mod.load_devices(cfg["devices_file"])  # fresh post-pull roster
    network_map = state["network_map"]
    method = state["method"]
    return _text(
        [
            {
                "name": d["name"],
                "mac": d["mac"],
                "ssh_user": d.get("ssh_user"),
                "ssh_target": f"{d['ssh_user']}@{network_map[d['mac']]}"
                    if d.get("ssh_user") and d["mac"] in network_map else None,
                "vendor": vendor_mod.get_vendor(d["mac"]),
                "online": d["mac"] in network_map,
                "ip": network_map.get(d["mac"]),
                "last_seen": seen_mod.get_last_seen(d["mac"]),
                "scan_method": method,
            }
            for d in roster
        ]
    )


async def _tool_get_device_ip(name: str) -> list[types.TextContent]:
    try:
        state = await _get_status(force=False)
        device = next((d for d in state["roster"] if d["name"] == name), None)
        if device is None:
            return _text({"name": name, "ip": None, "online": False, "ssh_user": None, "ssh_target": None})
        ip = state["network_map"].get(device["mac"])
        ssh_user = device.get("ssh_user")
        return _text({
            "name": name,
            "ip": ip,
            "online": ip is not None,
            "ssh_user": ssh_user,
            "ssh_target": f"{ssh_user}@{ip}" if ssh_user and ip else None,
        })
    except Exception:
        return _text({"name": name, "ip": None, "online": False, "ssh_user": None, "ssh_target": None})


async def _tool_register_device(name: str, mac: str, ssh_user: str | None = None) -> list[types.TextContent]:
    if not _NAME_RE.match(name):
        return _text(
            {
                "success": False,
                "name": name,
                "mac": mac,
                "message": "Invalid name. Use letters, digits, hyphens, and underscores only.",
            }
        )
    if not dev_mod.validate_mac(mac):
        return _text(
            {"success": False, "name": name, "mac": mac, "message": "Invalid MAC address format."}
        )
    norm_mac = dev_mod.normalize_mac(mac)
    cfg = _get_config()
    await asyncio.to_thread(git_ops.pull_repo, cfg["repo_path"])
    roster = dev_mod.load_devices(cfg["devices_file"])
    for d in roster:
        if d["name"] == name:
            return _text(
                {
                    "success": False,
                    "name": name,
                    "mac": norm_mac,
                    "message": f"A device named '{name}' already exists.",
                }
            )
        if d["mac"] == norm_mac:
            return _text(
                {
                    "success": False,
                    "name": name,
                    "mac": norm_mac,
                    "message": f"MAC {norm_mac} is already registered as '{d['name']}'.",
                }
            )
    entry: dict = {"name": name, "mac": norm_mac}
    if ssh_user:
        entry["ssh_user"] = ssh_user
    roster.append(entry)
    dev_mod.save_devices(cfg["devices_file"], roster)
    await asyncio.to_thread(
        git_ops.commit_and_push,
        cfg["repo_path"],
        [cfg["devices_file"]],
        f"Register device: {name} ({norm_mac})",
    )
    return _text(
        {
            "success": True,
            "name": name,
            "mac": norm_mac,
            "ssh_user": ssh_user,
            "message": f"Device '{name}' registered successfully.",
        }
    )


async def _tool_remove_device(name: str) -> list[types.TextContent]:
    cfg = _get_config()
    await asyncio.to_thread(git_ops.pull_repo, cfg["repo_path"])
    roster = dev_mod.load_devices(cfg["devices_file"])
    new_roster = [d for d in roster if d["name"] != name]
    if len(new_roster) == len(roster):
        return _text(
            {"success": False, "name": name, "message": f"Device '{name}' not found in roster."}
        )
    dev_mod.save_devices(cfg["devices_file"], new_roster)
    await asyncio.to_thread(
        git_ops.commit_and_push,
        cfg["repo_path"],
        [cfg["devices_file"]],
        f"Remove device: {name}",
    )
    return _text({"success": True, "name": name, "message": f"Device '{name}' removed successfully."})


async def _tool_find_unknown_devices() -> list[types.TextContent]:
    state = await _get_status(force=True)
    registered_macs = {d["mac"] for d in state["roster"]}
    return _text(
        [
            {"ip": ip, "mac": mac, "vendor": vendor_mod.get_vendor(mac)}
            for mac, ip in state["network_map"].items()
            if mac not in registered_macs
        ]
    )


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def serve():
    asyncio.run(main())
