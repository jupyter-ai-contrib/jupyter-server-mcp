"""Utilities for publishing and discovering MCP server runtime info.

The MCP extension writes a JSON file to Jupyter's runtime directory when it
starts, so that the stdio proxy can find running MCP servers without the user
having to hard-code a port. The file is named ``jpserver-mcp-<pid>.json``.

Discovery has to keep working even when the server and the client resolve
*different* Jupyter runtime directories. That happens whenever the server is
launched with an environment-local data dir — for example
``JUPYTER_DATA_DIR=$VIRTUAL_ENV/share/jupyter`` — while the client (the stdio
proxy) runs in a different environment and resolves the plain user-level
directory. To make the rendezvous robust, the extension publishes to, and the
proxy searches, a small set of candidate directories: the environment-resolved
``jupyter_runtime_dir()`` and the environment-independent user-level default
(see :func:`candidate_runtime_dirs`).
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

# Environment variables that relocate ``jupyter_runtime_dir()`` away from the
# stable, per-user default. They are cleared transiently to compute that
# default in :func:`default_runtime_dir`.
_RUNTIME_DIR_ENV_OVERRIDES = ("JUPYTER_RUNTIME_DIR", "JUPYTER_DATA_DIR")


def info_file_path(runtime_dir: str | os.PathLike[str], pid: int) -> Path:
    """Return the path to the MCP info file for ``pid`` in ``runtime_dir``."""
    return Path(runtime_dir) / f"{INFO_FILE_PREFIX}{pid}{INFO_FILE_SUFFIX}"


def default_runtime_dir() -> str:
    """Return the user-level Jupyter runtime dir, ignoring env overrides.

    ``jupyter_runtime_dir()`` honors ``$JUPYTER_RUNTIME_DIR`` and
    ``$JUPYTER_DATA_DIR``. A server started with an environment-local data dir
    (common in virtualenvs) therefore writes its info file where a client
    started in a *different* environment will not look. Resolving the path with
    those overrides removed yields a stable, per-user location both sides can
    agree on, independent of how either process was launched.

    The environment is restored before returning; the temporary mutation is
    confined to this synchronous call.
    """
    saved = {
        key: os.environ.pop(key)
        for key in _RUNTIME_DIR_ENV_OVERRIDES
        if key in os.environ
    }
    try:
        return jupyter_runtime_dir()
    finally:
        os.environ.update(saved)


def candidate_runtime_dirs(
    runtime_dir: str | os.PathLike[str] | None = None,
) -> list[Path]:
    """Return the runtime directories to publish to and search, in order.

    When ``runtime_dir`` is given it is used verbatim as the sole directory.
    Otherwise the environment-resolved :func:`jupyter_runtime_dir` and the
    environment-independent :func:`default_runtime_dir` are returned,
    de-duplicated (they coincide unless ``$JUPYTER_DATA_DIR`` /
    ``$JUPYTER_RUNTIME_DIR`` relocates the former).
    """
    if runtime_dir is not None:
        return [Path(runtime_dir)]

    dirs: list[Path] = []
    seen: set[str] = set()
    for candidate in (jupyter_runtime_dir(), default_runtime_dir()):
        path = Path(candidate)
        key = os.path.normcase(os.path.normpath(str(path)))
        if key not in seen:
            seen.add(key)
            dirs.append(path)
    return dirs


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


def _iter_dir_servers(directory: Path) -> Iterator[dict[str, Any]]:
    """Yield valid info dicts from a single ``directory``, pruning stale files.

    Info files whose owning process can no longer be found are unlinked as a
    side effect, mirroring ``list_running_servers`` in ``jupyter_server``.
    """
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


def list_running_mcp_servers(
    runtime_dir: str | os.PathLike[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield info dicts for every MCP server that appears to be running.

    Every directory returned by :func:`candidate_runtime_dirs` is scanned (or
    just ``runtime_dir`` when one is given). A server that published to more
    than one candidate directory is yielded only once, keyed by pid, with the
    higher-priority directory winning. Stale info files are unlinked as a side
    effect (see :func:`_iter_dir_servers`).
    """
    seen_pids: set[int] = set()
    for directory in candidate_runtime_dirs(runtime_dir):
        for info in _iter_dir_servers(directory):
            pid = info["pid"]
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            yield info
