"""MAC vendor (OUI) lookup. Requires optional dep mac-vendor-lookup."""

_lookup = None
_available: bool | None = None


def _get_lookup():
    global _lookup, _available
    if _available is None:
        try:
            from mac_vendor_lookup import MacLookup  # noqa: PLC0415
            _lookup = MacLookup()
            _available = True
        except ImportError:
            _available = False
    return _lookup if _available else None


def get_vendor(mac: str) -> str:
    lk = _get_lookup()
    if lk is None:
        return "—"
    try:
        return lk.lookup(mac)
    except Exception:
        return "—"


def is_available() -> bool:
    _get_lookup()
    return bool(_available)
