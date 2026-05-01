import ipaddress
import socket
import subprocess


def get_local_ip_and_network() -> tuple[str, str]:
    """Return (local_ip, cidr_network) for the default interface."""
    try:
        import netifaces
        gws = netifaces.gateways()
        iface = gws["default"][netifaces.AF_INET][1]
        addr = netifaces.ifaddresses(iface)[netifaces.AF_INET][0]
        ip, mask = addr["addr"], addr["netmask"]
        network = str(ipaddress.IPv4Network(f"{ip}/{mask}", strict=False))
        return ip, network
    except Exception:
        pass

    # Fallback: connect to external and read source IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    network = str(ipaddress.IPv4Network(f"{ip}/24", strict=False))
    return ip, network


def scan_network(network_cidr: str) -> list[tuple[str, str]]:
    """Return list of (ip, mac) found on the network."""
    try:
        return _scapy_arp_scan(network_cidr)
    except Exception:
        pass
    return _ping_arp_scan(network_cidr)


def _scapy_arp_scan(network_cidr: str) -> list[tuple[str, str]]:
    from scapy.all import ARP, Ether, srp  # noqa: PLC0415

    pkt = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=network_cidr)
    answered, _ = srp(pkt, timeout=3, verbose=0)
    return [(rcv.psrc, rcv.hwsrc.lower()) for _, rcv in answered]


def _ping_arp_scan(network_cidr: str) -> list[tuple[str, str]]:
    net = ipaddress.ip_network(network_cidr, strict=False)
    # Cap at /24 to avoid massive sweeps
    if net.prefixlen < 24:
        host_ip = list(net.hosts())[0] if net.num_addresses > 0 else None
        if host_ip:
            net = ipaddress.ip_network(f"{host_ip}/24", strict=False)

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

    # Linux: /proc/net/arp
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

    # Fallback: arp -n
    try:
        out = subprocess.check_output(["arp", "-n"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[2] not in ("(incomplete)", "<incomplete>"):
                results.append((parts[0], parts[2].lower()))
    except Exception:
        pass

    return results


def find_ip_by_mac(mac: str, scan_results: list[tuple[str, str]]) -> str | None:
    mac_lower = mac.lower()
    for ip, found_mac in scan_results:
        if found_mac == mac_lower:
            return ip
    return None
