"""Where this DEA was installed from, whether a newer one exists, and how to get it.

Three ways to have DEA on a machine:

* ``source``: a git checkout run with ``uv run dea`` (or ``pip install -e``). Updating is
  ``git pull`` plus ``uv sync``.
* ``tool``: installed with ``uv tool install "dea[all] @ git+<repo>"``. Updating is
  ``uv tool upgrade dea``.
* ``package``: anything else (``pip install git+...``). Updating reinstalls from git.

``dea update`` does the right one, ``dea serve --auto-update`` does it before starting, and
the web app shows a hint when the repository has moved on.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from dea import __version__

REPO = "DeastinY/diagnostic-expert-advisor"
GIT_URL = f"https://github.com/{REPO}"
SPEC = f"dea[all] @ git+{GIT_URL}"
DEFAULT_BRANCH = "main"


@dataclass
class Install:
    mode: str  # source | tool | package
    version: str
    commit: str | None = None
    branch: str | None = None
    root: str | None = None  # checkout root for ``source``

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _source_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    if (root / ".git").exists() and (root / "pyproject.toml").is_file():
        return root
    return None


def _direct_url() -> dict[str, Any]:
    try:
        raw = metadata.distribution("dea").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return {}
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _is_uv_tool() -> bool:
    parts = Path(__file__).resolve().parts
    return "tools" in parts and "uv" in " ".join(parts).lower()


def describe() -> Install:
    """How this DEA was installed, with the commit it was built from when known."""
    root = _source_root()
    if root is not None:
        return Install(
            mode="source",
            version=__version__,
            commit=_git(root, "rev-parse", "HEAD"),
            branch=_git(root, "rev-parse", "--abbrev-ref", "HEAD"),
            root=str(root),
        )
    info = _direct_url()
    vcs = info.get("vcs_info") or {}
    return Install(
        mode="tool" if _is_uv_tool() else "package",
        version=__version__,
        commit=vcs.get("commit_id"),
        branch=vcs.get("requested_revision") or (DEFAULT_BRANCH if vcs else None),
    )


def latest_commit(branch: str = DEFAULT_BRANCH, timeout: float = 4.0) -> str | None:
    """The newest commit on ``branch`` at GitHub, or ``None`` when offline."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/commits/{branch}",
        headers={"Accept": "application/vnd.github.sha", "User-Agent": f"dea/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
            return resp.read().decode().strip() or None
    except Exception:  # noqa: BLE001 (any network problem just means "unknown")
        return None


def check(
    timeout: float = 4.0, latest: Callable[[str], str | None] | None = None
) -> dict[str, Any]:
    """Installed commit versus the branch head. ``update_available`` is ``None`` when the
    comparison was not possible (offline, or no commit recorded)."""
    inst = describe()
    branch = inst.branch if inst.branch and inst.branch != "HEAD" else DEFAULT_BRANCH
    head = (latest or (lambda b: latest_commit(b, timeout)))(branch)
    available: bool | None = None
    if inst.commit and head:
        available = not head.startswith(inst.commit) and not inst.commit.startswith(head)
    return {
        **inst.as_dict(),
        "latest": head,
        "update_available": available,
        "command": update_command(inst),
    }


def update_command(inst: Install | None = None) -> list[list[str]]:
    """The shell commands ``update`` runs for this installation."""
    inst = inst or describe()
    uv = shutil.which("uv")
    if inst.mode == "source":
        root = inst.root or "."
        steps = [["git", "-C", root, "pull", "--ff-only"]]
        if uv and Path(root, "uv.lock").is_file():
            steps.append([uv, "sync", "--all-extras", "--project", root])
        else:
            steps.append([sys.executable, "-m", "pip", "install", "-e", f"{root}[all]"])
        return steps
    if inst.mode == "tool" and uv:
        return [[uv, "tool", "upgrade", "dea"]]
    return [[sys.executable, "-m", "pip", "install", "--upgrade", SPEC]]


def update(run: Callable[..., Any] = subprocess.run, quiet: bool = False) -> int:
    """Bring this installation up to date. Returns the first failing step's exit code."""
    inst = describe()
    for cmd in update_command(inst):
        if not quiet:
            print("$ " + " ".join(cmd))
        result = run(cmd)
        code = getattr(result, "returncode", result)
        if code:
            return int(code)
    return 0


def auto_update_requested(flag: bool) -> bool:
    return flag or os.environ.get("DEA_AUTO_UPDATE", "").lower() in {"1", "true", "yes"}
