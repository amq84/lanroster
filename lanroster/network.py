import ipaddress
import re
import socket
import subprocess
from dataclasses import dataclass


@dataclass
class ScanResult:
    hosts: list[tuple[str, str]]   # (ip, mac) pairs
    method: str                     # "scapy" | "nmap" | "ping+arp"
    complete: bool                  # False = may miss non-pingable hosts


def get_local_ip_and_network() -> tuple[str, str]:
    """Return (local_ip, cidr_network) for the default interface."""
    try:
        import netifaces  # noqa: PLC0415
        gws = netifaces.gateways()
        iface = gws["default"][netifaces.AF_INET][1]
        addr = netifaces.ifaddresses(iface)[netifaces.AF_INET][0]
        ip, mask = addr["addr"], addr["netmask"]
        return ip, str(ipaddress.IPv4Network(f"{ip}/{mask}", strict=False))
    except Exception:
        pass

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip, str(ipaddress.IPv4Network(f"{ip}/24", strict=False))


def scan_network(network_cidr: str) -> ScanResult:
    # 1. Scapy ARP — best: root, fast, guaranteed MAC+IP
    try:
        hosts = _scapy_arp_scan(network_cidr)
        return ScanResult(hosts=hosts, method="scapy", complete=True)
    except PermissionError:
        pass  # installed but no raw socket — fall through with degraded flag
    except ImportError:
        pass
    except Exception:
        pass

    # 2. nmap -sn — good: works without root, cross-refs ARP table for MACs
    nmap_hosts = _nmap_scan(network_cidr)
    if nmap_hosts is not None:
        return ScanResult(hosts=nmap_hosts, method="nmap", complete=False)

    # 3. Ping sweep + ARP table — last resort
    hosts = _ping_arp_scan(network_cidr)
    return ScanResult(hosts=hosts, method="ping+arp", complete=False)


def find_ip_by_mac(mac: str, result: ScanResult) -> str | None:
    mac_lower = mac.lower()
    for ip, found_mac in result.hosts:
        if found_mac == mac_lower:
            return ip
    return None


# ---------------------------------------------------------------------------
# Private scan backends
# ---------------------------------------------------------------------------

def _scapy_arp_scan(network_cidr: str) -> list[tuple[str, str]]:
    from scapy.all import ARP, Ether, srp  # noqa: PLC0415
    pkt = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=network_cidr)
    answered, _ = srp(pkt, timeout=3, verbose=0)
    return [(rcv.psrc, rcv.hwsrc.lower()) for _, rcv in answered]


def _nmap_scan(network_cidr: str) -> list[tuple[str, str]] | None:
    try:
        proc = subprocess.run(
            ["nmap", "-sn", network_cidr],
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    discovered_ips: list[str] = []
    mac_from_nmap: dict[str, str] = {}
    current_ip: str | None = None

    for line in proc.stdout.splitlines():
        m = re.search(r"Nmap scan report for (?:\S+ \()?(\d+\.\d+\.\d+\.\d+)\)?", line)
        if m:
            current_ip = m.group(1)
            discovered_ips.append(current_ip)
        m = re.search(r"MAC Address: ([0-9A-Fa-f:]{17})", line)
        if m and current_ip:
            mac_from_nmap[current_ip] = m.group(1).lower()

    if not discovered_ips:
        return None

    arp = dict(_read_arp_table())
    results = []
    for ip in discovered_ips:
        mac = mac_from_nmap.get(ip) or arp.get(ip)
        if mac:
            results.append((ip, mac))

    return results or None


def _ping_arp_scan(network_cidr: str) -> list[tuple[str, str]]:
    net = ipaddress.ip_network(network_cidr, strict=False)
    if net.prefixlen < 24:
        first_host = next(net.hosts(), None)
        if first_host:
            net = ipaddress.ip_network(f"{first_host}/24", strict=False)

    procs = [
        subprocess.Popen(
            ["ping", "-c", "1", "-W", "1", str(h)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for h in net.hosts()
    ]
    for p in procs:
        p.wait()

    return _read_arp_table()


def _read_arp_table() -> list[tuple[str, str]]:
    results = []

    try:
        with open("/proc/net/arp") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and parts[3] != "00:00:00:00:00:00":
                    results.append((parts[0], parts[3].lower()))
        if results:
            return results
    except Exception:
        pass

    try:
        out = subprocess.check_output(["arp", "-n"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[2] not in ("(incomplete)", "<incomplete>"):
                results.append((parts[0], parts[2].lower()))
    except Exception:
        pass

    return results
