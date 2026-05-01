import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".lanroster"
CONFIG_FILE = CONFIG_DIR / "config.json"


def get_config():
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def require_config():
    import click
    cfg = get_config()
    if cfg is None:
        raise click.ClickException(
            "Not initialized. Run 'lanroster init <repo_url>' first."
        )
    return cfg
