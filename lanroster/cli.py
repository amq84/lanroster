import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import config as cfg_mod
from . import devices as dev_mod
from . import git_ops
from . import network as net_mod
from . import seen as seen_mod
from . import vendor as vendor_mod

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_network(network_cidr_override: str | None) -> str:
    if network_cidr_override:
        return network_cidr_override
    _, cidr = net_mod.get_local_ip_and_network()
    return cidr


def _do_scan(network_cidr: str) -> net_mod.ScanResult:
    result = net_mod.scan_network(network_cidr)
    seen_mod.update_from_scan(result.hosts)
    return result


def _build_status_table(
    roster: list[dict],
    network_map: dict[str, str],
    show_vendor: bool = True,
    show_last_seen: bool = True,
) -> Table:
    table = Table(
        show_header=True,
        header_style="bold magenta",
        border_style="dim",
        show_lines=False,
        expand=False,
    )
    table.add_column("", width=2)
    table.add_column("Device", style="bold", min_width=12)
    table.add_column("MAC Address", style="dim")
    if show_vendor:
        table.add_column("Vendor", style="dim", max_width=22)
    table.add_column("IP Address", min_width=14)
    if show_last_seen:
        table.add_column("Last Seen", style="dim", min_width=9)

    for device in roster:
        mac = device["mac"].lower()
        ip = network_map.get(mac)
        indicator = Text("●", style="bold green") if ip else Text("○", style="red dim")
        ip_cell = Text(ip, style="cyan") if ip else Text("—", style="dim")

        row = [indicator, device["name"], device["mac"]]
        if show_vendor:
            row.append(vendor_mod.get_vendor(device["mac"]))
        row.append(ip_cell)
        if show_last_seen:
            ts = seen_mod.get_last_seen(mac)
            row.append(seen_mod.relative(ts) if ts else "—")

        table.add_row(*row)

    return table


def _summary_chart(online: int, offline: int) -> str:
    total = online + offline
    bar_w = 28

    def bar(count: int, color: str) -> str:
        filled = round(bar_w * count / total) if total else 0
        empty = bar_w - filled
        return f"[{color}]{'█' * filled}[/{color}][bright_black]{'░' * empty}[/bright_black]"

    on_pct = round(100 * online / total) if total else 0
    return "\n".join([
        f"[green]Online [/green] {bar(online,  'green')}  {online}/{total} ({on_pct}%)",
        f"[red]Offline[/red] {bar(offline, 'red'  )}  {offline}/{total} ({100 - on_pct}%)",
    ])


def _send_notification(title: str, body: str) -> None:
    import platform
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(
                ["osascript", "-e", f'display notification "{body}" with title "lanroster: {title}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["notify-send", "--icon=network-wired", f"lanroster: {title}", body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass


import subprocess  # noqa: E402  (needed by _send_notification above)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="lanroster")
def cli():
    """LanRoster — manage and monitor your network device roster."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("repo_url")
def init(repo_url):
    """Initialize lanroster from a device-list repository REPO_URL."""
    repo_path = Path.home() / ".lanroster" / "repo"

    if repo_path.exists():
        console.print("[yellow]Repository already present — pulling latest…[/yellow]")
        try:
            git_ops.pull_repo(repo_path)
            console.print("[green]✓ Repository updated[/green]")
        except Exception as exc:
            raise click.ClickException(f"Pull failed: {exc}") from exc
    else:
        console.print(f"Cloning [cyan]{repo_url}[/cyan] …")
        try:
            git_ops.clone_repo(repo_url, repo_path)
            console.print("[green]✓ Repository cloned[/green]")
        except Exception as exc:
            raise click.ClickException(f"Clone failed: {exc}") from exc

    devices_file = repo_path / "devices.json"
    if not devices_file.exists():
        console.print("[yellow]devices.json not found — creating empty roster[/yellow]")
        with open(devices_file, "w") as f:
            json.dump({"devices": []}, f, indent=2)
            f.write("\n")

    cfg_mod.save_config({
        "repo_url": repo_url,
        "repo_path": str(repo_path),
        "devices_file": str(devices_file),
    })
    console.print(f"[bold green]✓ Initialized.[/bold green]  Roster: [dim]{devices_file}[/dim]")


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("name")
@click.argument("mac")
@click.option("--user", "-u", default=None, metavar="USER",
              help="SSH username for this device (e.g. root, pi, abel).")
def register(name, mac, user):
    """Register a new device NAME with its MAC address."""
    cfg = cfg_mod.require_config()

    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise click.ClickException(
            "Name must contain only letters, digits, hyphens, and underscores."
        )
    if not dev_mod.validate_mac(mac):
        raise click.ClickException(
            f"Invalid MAC address '{mac}'. Expected format: aa:bb:cc:dd:ee:ff"
        )

    normalized = dev_mod.normalize_mac(mac)

    console.print("[cyan]Pulling latest roster…[/cyan]")
    try:
        git_ops.pull_repo(cfg["repo_path"])
    except Exception as exc:
        console.print(f"[yellow]Warning: pull failed ({exc}) — continuing with local copy[/yellow]")

    roster = dev_mod.load_devices(cfg["devices_file"])
    for d in roster:
        if d["name"] == name:
            raise click.ClickException(f"Name '{name}' is already registered.")
        if dev_mod.normalize_mac(d["mac"]) == normalized:
            raise click.ClickException(
                f"MAC {normalized} is already registered as '{d['name']}'."
            )

    entry: dict = {"name": name, "mac": normalized}
    if user:
        entry["ssh_user"] = user
    roster.append(entry)
    dev_mod.save_devices(cfg["devices_file"], roster)

    console.print("[cyan]Committing and pushing…[/cyan]")
    try:
        git_ops.commit_and_push(
            cfg["repo_path"],
            ["devices.json"],
            f"Register device: {name} ({normalized})",
        )
    except Exception as exc:
        raise click.ClickException(f"Git push failed: {exc}") from exc

    vendor = vendor_mod.get_vendor(normalized)
    vendor_str = f" [dim]({vendor})[/dim]" if vendor != "—" else ""
    user_str = f" [dim]ssh_user={user}[/dim]" if user else ""
    console.print(
        f"[bold green]✓ Registered[/bold green] [bold]{name}[/bold]{vendor_str} — [cyan]{normalized}[/cyan]{user_str}"
    )


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def remove(name, yes):
    """Remove device NAME from the roster."""
    cfg = cfg_mod.require_config()

    console.print("[cyan]Pulling latest roster…[/cyan]")
    try:
        git_ops.pull_repo(cfg["repo_path"])
    except Exception as exc:
        console.print(f"[yellow]Warning: pull failed ({exc}) — continuing with local copy[/yellow]")

    roster = dev_mod.load_devices(cfg["devices_file"])
    device = next((d for d in roster if d["name"] == name), None)
    if device is None:
        raise click.ClickException(f"Device '{name}' not found in roster.")

    if not yes:
        click.confirm(
            f"Remove '{name}' ({device['mac']}) from the roster?", abort=True
        )

    updated = [d for d in roster if d["name"] != name]
    dev_mod.save_devices(cfg["devices_file"], updated)

    console.print("[cyan]Committing and pushing…[/cyan]")
    try:
        git_ops.commit_and_push(
            cfg["repo_path"],
            ["devices.json"],
            f"Remove device: {name} ({device['mac']})",
        )
    except Exception as exc:
        raise click.ClickException(f"Git push failed: {exc}") from exc

    console.print(f"[bold green]✓ Removed[/bold green] '{name}' from the roster.")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@cli.command("list")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON.")
def list_devices(as_json):
    """List all registered devices without scanning the network."""
    cfg = cfg_mod.require_config()
    roster = dev_mod.load_devices(cfg["devices_file"])

    if as_json:
        out = []
        for d in roster:
            ts = seen_mod.get_last_seen(d["mac"])
            out.append({
                "name": d["name"],
                "mac": d["mac"],
                "ssh_user": d.get("ssh_user"),
                "vendor": vendor_mod.get_vendor(d["mac"]),
                "last_seen": ts,
            })
        click.echo(json.dumps(out, indent=2))
        return

    if not roster:
        console.print("[yellow]Roster is empty. Use 'lanroster register' to add devices.[/yellow]")
        return

    has_ssh = any(d.get("ssh_user") for d in roster)
    table = Table(
        title=f"[bold]Registered Devices[/bold] [dim]({len(roster)} total)[/dim]",
        show_header=True,
        header_style="bold magenta",
        border_style="dim",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold")
    table.add_column("MAC Address", style="dim")
    table.add_column("Vendor", style="dim", max_width=24)
    if has_ssh:
        table.add_column("SSH User", style="cyan")
    table.add_column("Last Seen", style="dim")

    for i, d in enumerate(roster, 1):
        ts = seen_mod.get_last_seen(d["mac"])
        row = [str(i), d["name"], d["mac"], vendor_mod.get_vendor(d["mac"])]
        if has_ssh:
            row.append(d.get("ssh_user") or "—")
        row.append(seen_mod.relative(ts) if ts else "—")
        table.add_row(*row)

    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--network", "network_cidr", default=None, metavar="CIDR",
              help="Override detected subnet (e.g. 10.0.0.0/24).")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON.")
@click.option("--pull/--no-pull", default=True, show_default=True,
              help="Pull roster repo before scanning.")
def status(network_cidr, as_json, pull):
    """Discover the network and show each device's connection status."""
    cfg = cfg_mod.require_config()

    if pull:
        try:
            git_ops.pull_repo(cfg["repo_path"])
        except Exception as exc:
            if not as_json:
                console.print(f"[yellow]Warning: pull failed ({exc})[/yellow]")

    roster = dev_mod.load_devices(cfg["devices_file"])
    if not roster:
        if not as_json:
            console.print("[yellow]Roster is empty. Use 'lanroster register' to add devices.[/yellow]")
        else:
            click.echo("[]")
        return

    network_map: dict[str, str] = {}
    scan_method = "none"

    if not as_json:
        with console.status("[cyan]Scanning network…[/cyan]", spinner="dots"):
            try:
                result = _do_scan(_resolve_network(network_cidr))
                network_map = {mac: ip for ip, mac in result.hosts}
                scan_method = result.method
            except Exception as exc:
                console.print(f"[yellow]Scan warning: {exc}[/yellow]")
    else:
        try:
            result = _do_scan(_resolve_network(network_cidr))
            network_map = {mac: ip for ip, mac in result.hosts}
            scan_method = result.method
        except Exception:
            pass

    if as_json:
        out = []
        for d in roster:
            mac = d["mac"].lower()
            ip = network_map.get(mac)
            ts = seen_mod.get_last_seen(mac)
            ssh_user = d.get("ssh_user")
            out.append({
                "name": d["name"],
                "mac": d["mac"],
                "ssh_user": ssh_user,
                "ssh_target": f"{ssh_user}@{ip}" if ssh_user and ip else None,
                "vendor": vendor_mod.get_vendor(d["mac"]),
                "online": ip is not None,
                "ip": ip,
                "last_seen": ts,
            })
        click.echo(json.dumps(out, indent=2))
        return

    if scan_method not in ("scapy", "none"):
        console.print(
            f"[yellow]Note:[/yellow] scan used [bold]{scan_method}[/bold] — "
            "results may be incomplete. Run as root or install scapy for full ARP scan."
        )

    table = _build_status_table(roster, network_map)
    table.title = "[bold]LanRoster — Network Status[/bold]"
    online = sum(1 for d in roster if network_map.get(d["mac"].lower()))

    console.print()
    console.print(table)
    console.print()
    console.print(Panel(_summary_chart(online, len(roster) - online), title="Summary", expand=False))


# ---------------------------------------------------------------------------
# ip
# ---------------------------------------------------------------------------

@cli.command("ip")
@click.argument("name")
@click.option("--network", "network_cidr", default=None, metavar="CIDR",
              help="Override detected subnet.")
def get_ip(name, network_cidr):
    """Print the IP of device NAME — designed for use in shell scripts.

    \b
    Example:
        ssh user@$(lanroster ip my-server)

    Exits with status 1 if the device is not reachable.
    """
    cfg = cfg_mod.require_config()
    roster = dev_mod.load_devices(cfg["devices_file"])

    device = next((d for d in roster if d["name"] == name), None)
    if device is None:
        click.echo(f"Error: device '{name}' not in roster.", err=True)
        sys.exit(2)

    try:
        result = _do_scan(_resolve_network(network_cidr))
    except Exception as exc:
        click.echo(f"Error: network scan failed: {exc}", err=True)
        sys.exit(2)

    ip = net_mod.find_ip_by_mac(device["mac"], result)
    if ip is None:
        sys.exit(1)

    print(ip)


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--interval", "-i", default=60, show_default=True, type=int,
              help="Seconds between scans.")
@click.option("--network", "network_cidr", default=None, metavar="CIDR",
              help="Override detected subnet.")
def watch(interval, network_cidr):
    """Continuously monitor device status and alert on transitions.

    Sends a desktop notification (notify-send / osascript) when a device
    comes online or goes offline.
    """
    cfg = cfg_mod.require_config()

    previous: dict[str, bool] = {}
    events: list[str] = []

    def do_cycle() -> tuple[list[dict], dict[str, str], str]:
        roster = dev_mod.load_devices(cfg["devices_file"])
        result = _do_scan(_resolve_network(network_cidr))
        nmap = {mac: ip for ip, mac in result.hosts}
        return roster, nmap, result.method

    def make_display(
        roster: list[dict],
        network_map: dict[str, str],
        method: str,
        scan_time: str,
    ):
        table = _build_status_table(roster, network_map, show_vendor=True, show_last_seen=True)
        online = sum(1 for d in roster if network_map.get(d["mac"].lower()))

        header = (
            f"[bold]lanroster watch[/bold]  "
            f"[dim]interval={interval}s  method={method}  "
            f"last scan={scan_time}[/dim]  "
            f"[green]{online}[/green]/[white]{len(roster)}[/white] online"
        )
        event_body = "\n".join(events[-15:]) if events else "[dim]No transitions yet[/dim]"

        return Group(
            Panel(header, border_style="dim", padding=(0, 1)),
            table,
            Panel(event_body, title="Transitions", border_style="dim", padding=(0, 1)),
        )

    console.print(f"[cyan]Starting watch — scanning every {interval}s. Ctrl+C to stop.[/cyan]\n")

    try:
        with Live(screen=True, refresh_per_second=2, console=console) as live:
            first = True
            while True:
                scan_time = datetime.now().strftime("%H:%M:%S")
                try:
                    roster, network_map, method = do_cycle()
                except Exception as exc:
                    events.append(f"[{scan_time}] [red]Scan error: {exc}[/red]")
                    time.sleep(interval)
                    continue

                current = {
                    d["name"]: (bool(network_map.get(d["mac"].lower())),
                                network_map.get(d["mac"].lower()))
                    for d in roster
                }

                if not first:
                    for dname, (online, ip) in current.items():
                        was_online = previous.get(dname)
                        if was_online is None:
                            continue
                        if online and not was_online:
                            msg = f"[{scan_time}] [green]▲ {dname} came online ({ip})[/green]"
                            events.append(msg)
                            _send_notification(f"{dname} online", ip or "")
                        elif not online and was_online:
                            events.append(f"[{scan_time}] [red]▼ {dname} went offline[/red]")
                            _send_notification(f"{dname} offline", "was online")

                previous = {n: on for n, (on, _) in current.items()}
                first = False

                live.update(make_display(roster, network_map, method, scan_time))
                time.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[dim]Watch stopped.[/dim]")


# ---------------------------------------------------------------------------
# ssh
# ---------------------------------------------------------------------------

@cli.command("ssh")
@click.argument("name")
@click.option("--user", "-u", default=None, metavar="USER",
              help="Override the stored SSH username.")
@click.option("--network", "network_cidr", default=None, metavar="CIDR",
              help="Override detected subnet.")
def ssh_connect(name, user, network_cidr):
    """Open an SSH session to device NAME.

    \b
    Example:
        lanroster ssh riu9-rpi4b
    """
    import os

    cfg = cfg_mod.require_config()
    roster = dev_mod.load_devices(cfg["devices_file"])

    device = next((d for d in roster if d["name"] == name), None)
    if device is None:
        raise click.ClickException(f"Device '{name}' not in roster.")

    with console.status("[cyan]Scanning network…[/cyan]", spinner="dots"):
        try:
            result = _do_scan(_resolve_network(network_cidr))
        except Exception as exc:
            raise click.ClickException(f"Network scan failed: {exc}") from exc

    ip = net_mod.find_ip_by_mac(device["mac"], result)
    if ip is None:
        raise click.ClickException(f"Device '{name}' is not reachable on the network.")

    ssh_user = user or device.get("ssh_user")
    target = f"{ssh_user}@{ip}" if ssh_user else ip
    console.print(f"[dim]Connecting to[/dim] [cyan]{target}[/cyan] …")
    os.execvp("ssh", ["ssh", target])


# ---------------------------------------------------------------------------
# web
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--port", "-p", default=5577, show_default=True, type=int,
              help="Port to listen on.")
@click.option("--interval", "-i", default=30, show_default=True, type=int,
              help="Seconds between network scans.")
@click.option("--no-browser", is_flag=True, default=False,
              help="Don't open the browser automatically.")
def web(port, interval, no_browser):
    """Start a live web dashboard (lanroster watch in the browser)."""
    from .web_server import run_web
    run_web(port=port, interval=interval, open_browser=not no_browser)


def main():
    cli()
