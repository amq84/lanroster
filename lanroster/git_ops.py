import subprocess
from pathlib import Path


def _run(args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def clone_repo(url: str, dest: Path) -> None:
    _run(["git", "clone", url, str(dest)])


def pull_repo(repo_path: Path) -> None:
    _run(["git", "-C", str(repo_path), "pull"])


def commit_and_push(repo_path: Path, files: list, message: str) -> None:
    for f in files:
        _run(["git", "-C", str(repo_path), "add", str(f)])
    _run(["git", "-C", str(repo_path), "commit", "-m", message])
    _run(["git", "-C", str(repo_path), "push"])
