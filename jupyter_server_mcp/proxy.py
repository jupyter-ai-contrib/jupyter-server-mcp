"""Stdio MCP proxy bridging an MCP client to a running Jupyter MCP server.

The extension writes a runtime info file when it starts (see
``jupyter_server_mcp.runtime``). This module reads those files, picks the
server that best matches the current working directory, and forwards MCP
traffic from stdio to the server's HTTP endpoint.

Run it with ``python -m jupyter_server_mcp.proxy``. MCP clients only need
this stable command, regardless of which port the server is actually using.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastmcp.server import create_proxy

from .runtime import list_running_mcp_servers

ENV_URL = "JUPYTER_SERVER_MCP_URL"

logger = logging.getLogger(__name__)


class ProxyError(RuntimeError):
    """Raised when the proxy cannot find or connect to a Jupyter MCP server."""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jupyter_server_mcp.proxy",
        description=(
            "Bridge stdio MCP traffic to a running Jupyter MCP server. "
            "Auto-discovers the server by reading jpserver-mcp-*.json files "
            "in the Jupyter runtime directory."
        ),
    )
    parser.add_argument(
        "--url",
        default=None,
        help=(
            "Explicit MCP endpoint URL (e.g. http://localhost:3001/mcp). "
            "Overrides auto-discovery. Also settable via the "
            f"{ENV_URL} environment variable."
        ),
    )
    parser.add_argument(
        "--runtime-dir",
        default=None,
        help=(
            "Override the Jupyter runtime directory searched for info files. "
            "Defaults to jupyter_core.paths.jupyter_runtime_dir()."
        ),
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help=(
            "Directory used when disambiguating between multiple running "
            "servers. Defaults to the current working directory."
        ),
    )
    return parser.parse_args(argv)


def _is_ancestor(ancestor: Path, descendant: Path) -> bool:
    """Return True if ``ancestor`` equals or contains ``descendant``."""
    try:
        descendant.relative_to(ancestor)
    except ValueError:
        return False
    return True


def _match_score(root_dir: Any, cwd: Path) -> int | None:
    """Return a specificity score if ``cwd`` lives under ``root_dir``.

    Higher scores mean a more specific (deeper) match. ``None`` means
    ``root_dir`` is missing, invalid, or does not contain ``cwd``.
    """
    if not isinstance(root_dir, str) or not root_dir:
        return None
    try:
        root_path = Path(root_dir).resolve()
    except OSError:
        return None
    if not _is_ancestor(root_path, cwd):
        return None
    return len(root_path.parts)


def _describe(server: dict[str, Any]) -> str:
    """Return a short human-readable description of a discovered server."""
    return (
        f"url={server.get('url')!r} "
        f"root_dir={server.get('root_dir')!r} "
        f"pid={server.get('pid')}"
    )


def select_server(servers: list[dict[str, Any]], cwd: Path) -> dict[str, Any]:
    """Pick the MCP server that best matches ``cwd``.

    Selection rules:
      * Zero servers → :class:`ProxyError`.
      * Exactly one server → return it unconditionally, even if ``cwd`` is
        not below its ``root_dir`` (assumes the user just wants to connect).
      * Multiple servers → score each candidate by how deep its ``root_dir``
        sits above ``cwd`` and pick the most specific. Ambiguous or missing
        matches raise :class:`ProxyError` with a listing, so the user can
        disambiguate via ``--url`` or the ``JUPYTER_SERVER_MCP_URL`` env var.
    """
    if not servers:
        msg = (
            "No running Jupyter MCP servers were discovered. Start Jupyter "
            "Server with the jupyter-server-mcp extension, or pass --url / "
            f"set ${ENV_URL}."
        )
        raise ProxyError(msg)

    if len(servers) == 1:
        return servers[0]

    scored = [
        (score, server)
        for server in servers
        for score in [_match_score(server.get("root_dir"), cwd)]
        if score is not None
    ]

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        top_score = scored[0][0]
        top_matches = [server for score, server in scored if score == top_score]
        if len(top_matches) == 1:
            return top_matches[0]
        candidates = top_matches
    else:
        candidates = servers

    listing = "\n".join(f"  - {_describe(server)}" for server in candidates)
    reason = (
        "Multiple Jupyter MCP servers match the current working directory"
        if scored
        else "Multiple Jupyter MCP servers are running and none contains the "
        "current working directory"
    )
    msg = f"{reason}. Pick one explicitly with --url or ${ENV_URL}:\n{listing}"
    raise ProxyError(msg)


def _validate_explicit_url(url: str, source: str) -> str:
    """Ensure an externally-supplied URL has an http(s) scheme."""
    if "://" not in url:
        msg = (
            f"{source} must be an absolute http(s) URL "
            f"(e.g. http://localhost:3001/mcp), got: {url!r}"
        )
        raise ProxyError(msg)
    scheme = url.split("://", 1)[0].lower()
    if scheme not in {"http", "https"}:
        msg = f"{source} must use http or https, got scheme {scheme!r}: {url!r}"
        raise ProxyError(msg)
    return url


def resolve_url(
    url: str | None = None,
    runtime_dir: str | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> str:
    """Resolve the MCP URL from explicit input or runtime discovery."""
    if url:
        return _validate_explicit_url(url, source="--url")
    env_url = os.environ.get(ENV_URL)
    if env_url:
        return _validate_explicit_url(env_url, source=f"${ENV_URL}")

    cwd_path = Path(cwd if cwd is not None else Path.cwd()).resolve()
    servers = list(list_running_mcp_servers(runtime_dir))
    server = select_server(servers, cwd_path)

    endpoint = server.get("url")
    if not isinstance(endpoint, str) or not endpoint:
        msg = f"Discovered MCP server info has no URL: {server!r}"
        raise ProxyError(msg)

    logger.info(
        "Discovered MCP server: %s (root_dir=%s, pid=%s)",
        endpoint,
        server.get("root_dir"),
        server.get("pid"),
    )
    return endpoint


async def run_proxy(url: str) -> None:
    """Run a FastMCP proxy to ``url`` over stdio."""
    proxy = create_proxy(url)
    await proxy.run_async(transport="stdio", show_banner=False)


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m jupyter_server_mcp.proxy``."""
    # MCP stdio traffic occupies stdout, so keep all logging on stderr.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)

    args = _parse_args(argv)

    try:
        url = resolve_url(args.url, args.runtime_dir, args.cwd)
    except ProxyError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    try:
        asyncio.run(run_proxy(url))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
