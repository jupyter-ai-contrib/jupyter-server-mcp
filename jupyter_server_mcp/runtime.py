"""Utilities for publishing and discovering MCP server runtime info.

The MCP extension writes a JSON file to Jupyter's runtime directory when it
starts, so that the stdio proxy can find running MCP servers without the user
having to hard-code a port. The file is named ``jpserver-mcp-<pid>.json`` and
uses the same directory as ``list_running_servers`` for consistency.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from jupyter_core.paths import jupyter_runtime_dir
from jupyter_server.utils import check_pid

INFO_FILE_PREFIX = "jpserver-mcp-"
INFO_FILE_SUFFIX = ".json"


def info_file_path(runtime_dir: str | os.PathLike[str], pid: int) -> Path:
    """Return the path to the MCP info file for ``pid`` in ``runtime_dir``."""
    return Path(runtime_dir) / f"{INFO_FILE_PREFIX}{pid}{INFO_FILE_SUFFIX}"


def write_info_file(path: str | os.PathLike[str], info: dict[str, Any]) -> None:
    """Write ``info`` as JSON to ``path`` atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def remove_info_file(path: str | os.PathLike[str]) -> None:
    """Remove the info file at ``path`` if it exists."""
    with contextlib.suppress(FileNotFoundError):
        Path(path).unlink()


def list_running_mcp_servers(
    runtime_dir: str | os.PathLike[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield info dicts for every MCP server that appears to be running.

    Stale info files — those whose owning process can no longer be found —
    are unlinked as a side effect, mirroring ``list_running_servers`` in
    ``jupyter_server``.
    """
    directory = Path(jupyter_runtime_dir() if runtime_dir is None else runtime_dir)
    if not directory.is_dir():
        return

    for entry in sorted(directory.iterdir()):
        name = entry.name
        if not (name.startswith(INFO_FILE_PREFIX) and name.endswith(INFO_FILE_SUFFIX)):
            continue

        try:
            raw = entry.read_text(encoding="utf-8")
        except OSError:
            continue

        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            continue

        pid = info.get("pid")
        if not isinstance(pid, int) or not check_pid(pid):
            with contextlib.suppress(OSError):
                entry.unlink()
            continue

        info["info_file"] = str(entry)
        yield info
