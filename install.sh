#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== lanroster installer ==="

# Check Python 3.10+
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.10 or later." >&2
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor}")')
if [ "$PY_VERSION" -lt 310 ]; then
    echo "ERROR: Python 3.10+ required (found $(python3 --version))." >&2
    exit 1
fi

# Install (prefer pipx for isolated install, fall back to pip)
if command -v pipx &>/dev/null; then
    echo "Installing with pipx (isolated environment)…"
    pipx install "$SCRIPT_DIR"
else
    echo "Installing with pip…"
    pip3 install --user "$SCRIPT_DIR"
fi

echo ""
echo "Done! Run 'lanroster --help' to get started."
echo ""
echo "Quick start:"
echo "  lanroster init <repo_url>          # point to your device-list repo"
echo "  lanroster register <name> <mac>    # add this machine"
echo "  lanroster status                   # see who's online"
echo "  lanroster ip <name>                # get IP for scripting"
