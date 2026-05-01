import json
import re
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import config as cfg_mod
from . import devices as dev_mod
from . import git_ops
from . import network as net_mod

console = Console()


@click.group()
@click.version_option(package_name="lanroster")
def cli():
    """LanRoster - manage and monitor your network device roster."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("repo_url")
def init(repo_url):
    """Initialize lanroster from a device-list repository REPO_URL."""
    repo_path = Path.home() / ".lanroster" / "repo"

    if repo_path.exists():
        console.print("[yellow]Repository already cloned — pulling latest...[/yellow]")
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

    cfg_mod.save_config(
        {
            "repo_url": repo_url,
            "repo_path": str(repo_path),
            "devices_file": str(devices_file),
        }
    )
    console.print(f"[bold green]✓ Initialized.[/bold green] Devices file: {devices_file}")


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("name")
@click.argument("mac")
def register(name, mac):
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
        console.print(f"[yellow]Warning: pull failed ({exc}), continuing with local copy[/yellow]")

    roster = dev_mod.load_devices(cfg["devices_file"])
    for d in roster:
        if d["name"] == name:
            raise click.ClickException(f"Device name '{name}' is already registered.")
        if dev_mod.normalize_mac(d["mac"]) == normalized:
            raise click.ClickException(
                f"MAC {normalized} is already registered as '{d['name']}'."
            )

    roster.append({"name": name, "mac": normalized})
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

    console.print(f"[bold green]✓ Registered '{name}'[/bold green] — MAC [cyan]{normalized}[/cyan]")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--no-scan", is_flag=True, default=False, help="Skip network scan, show roster only.")
def status(no_scan):
    """Discover the network and show each device's connection status."""
    cfg = cfg_mod.require_config()
    roster = dev_mod.load_devices(cfg["devices_file"])

    if not roster:
        console.print("[yellow]Roster is empty. Use 'lanroster register' to add devices.[/yellow]")
        return

    network_map: dict[str, str] = {}  # mac -> ip

    if not no_scan:
        with console.status("[cyan]Scanning network…[/cyan]", spinner="dots"):
            try:
                _, network_cidr = net_mod.get_local_ip_and_network()
                scan_results = net_mod.scan_network(network_cidr)
                for ip, mac in scan_results:
                    network_map[mac.lower()] = ip
            except Exception as exc:
                console.print(f"[yellow]Network scan warning: {exc}[/yellow]")

    # Build table
    table = Table(
        title="[bold]LanRoster — Network Status[/bold]",
        show_header=True,
        header_style="bold magenta",
        border_style="dim",
        show_lines=False,
    )
    table.add_column("", width=2)
    table.add_column("Device", style="bold")
    table.add_column("MAC Address", style="dim")
    table.add_column("IP Address")

    online_count = 0
    for device in roster:
        mac = device["mac"].lower()
        ip = network_map.get(mac)
        if ip:
            indicator = Text("●", style="bold green")
            ip_cell = Text(ip, style="cyan")
            online_count += 1
        else:
            indicator = Text("○", style="red dim")
            ip_cell = Text("—", style="dim")
        table.add_row(indicator, device["name"], device["mac"], ip_cell)

    console.print()
    console.print(table)
    console.print()

    total = len(roster)
    offline_count = total - online_count
    console.print(Panel(_summary_chart(online_count, offline_count), title="Summary", expand=False))


def _summary_chart(online: int, offline: int) -> str:
    total = online + offline
    bar_w = 28

    def bar(count, color, empty_color="bright_black"):
        filled = round(bar_w * count / total) if total else 0
        empty = bar_w - filled
        return f"[{color}]{'█' * filled}[/{color}][{empty_color}]{'░' * empty}[/{empty_color}]"

    on_pct = round(100 * online / total) if total else 0
    off_pct = 100 - on_pct

    lines = [
        f"[green]Online [/green] {bar(online,  'green')}  {online}/{total} ({on_pct}%)",
        f"[red]Offline[/red] {bar(offline, 'red'  )}  {offline}/{total} ({off_pct}%)",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ip
# ---------------------------------------------------------------------------

@cli.command("ip")
@click.argument("name")
def get_ip(name):
    """Print the current IP address of device NAME (for use in scripts).

    Exits with status 1 if the device is not found in the network.
    """
    cfg = cfg_mod.require_config()
    roster = dev_mod.load_devices(cfg["devices_file"])

    device = next((d for d in roster if d["name"] == name), None)
    if device is None:
        raise click.ClickException(f"Device '{name}' not in roster.")

    try:
        _, network_cidr = net_mod.get_local_ip_and_network()
        scan_results = net_mod.scan_network(network_cidr)
    except Exception as exc:
        raise click.ClickException(f"Network scan failed: {exc}") from exc

    found_ip = net_mod.find_ip_by_mac(device["mac"], scan_results)
    if found_ip is None:
        sys.exit(1)

    print(found_ip)


def main():
    cli()
