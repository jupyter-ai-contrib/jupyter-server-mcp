"""Stdio MCP proxy bridging an MCP client to a running Jupyter MCP server.

The extension writes a runtime info file when it starts (see
``jupyter_server_mcp.runtime``). This module reads those files, picks the
server that best matches the current working directory, and forwards MCP
traffic from stdio to the server's HTTP endpoint, over TCP or over the
server's Unix domain socket.

Run it with ``python -m jupyter_server_mcp.proxy``. MCP clients only need
this stable command, regardless of which port the server is actually using.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy

from .runtime import list_running_mcp_servers

ENV_URL = "JUPYTER_SERVER_MCP_URL"
#: URL used over a Unix domain socket. The host only fills in the Host header,
#: which the server's Host/Origin protection accepts for localhost.
UDS_URL = "http://localhost/mcp"

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
        "--uds",
        default=None,
        help=(
            "Connect over this Unix domain socket (see "
            "MCPExtensionApp.mcp_uds) instead of TCP. Overrides "
            "auto-discovery."
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
    location = (
        f"uds={server['uds']!r}" if server.get("uds") else f"url={server.get('url')!r}"
    )
    return f"{location} root_dir={server.get('root_dir')!r} pid={server.get('pid')}"


def select_server(servers: list[dict[str, Any]], cwd: Path) -> dict[str, Any]:
    """Pick the MCP server that best matches ``cwd``.

    Selection rules:
      * Zero servers → :class:`ProxyError`.
      * Exactly one server → return it unconditionally, even if ``cwd`` is
        not below its ``root_dir`` (assumes the user just wants to connect).
      * Multiple servers → score each candidate by how deep its ``root_dir``
        sits above ``cwd`` and pick the most specific. Ambiguous or missing
        matches raise :class:`ProxyError` with a listing, so the user can
        disambiguate via ``--url``, ``--uds``, or the ``JUPYTER_SERVER_MCP_URL``
        env var.
    """
    if not servers:
        msg = (
            "No running Jupyter MCP servers were discovered. Start Jupyter "
            "Server with the jupyter-server-mcp extension, or pass --url / "
            f"--uds / set ${ENV_URL}."
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
    msg = f"{reason}. Pick one explicitly with --url, --uds, or ${ENV_URL}:\n{listing}"
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


def resolve_endpoint(
    url: str | None = None,
    uds: str | None = None,
    runtime_dir: str | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> tuple[str, str | None]:
    """Resolve the MCP endpoint from explicit input or runtime discovery.

    Returns a ``(url, uds)`` pair, where ``uds`` is the Unix domain socket to
    send requests through, or ``None`` to connect to ``url`` over TCP.
    """
    if uds:
        return (_validate_explicit_url(url, source="--url") if url else UDS_URL), uds
    if url:
        return _validate_explicit_url(url, source="--url"), None
    env_url = os.environ.get(ENV_URL)
    if env_url:
        return _validate_explicit_url(env_url, source=f"${ENV_URL}"), None

    cwd_path = Path(cwd if cwd is not None else Path.cwd()).resolve()
    servers = list(list_running_mcp_servers(runtime_dir))
    server = select_server(servers, cwd_path)
    logger.info("Discovered MCP server: %s", _describe(server))

    # Servers on a Unix socket publish no URL, so that older proxies fail
    # instead of connecting over TCP.
    if server.get("uds"):
        return UDS_URL, server["uds"]

    endpoint = server.get("url")
    if not isinstance(endpoint, str) or not endpoint:
        msg = f"Discovered MCP server info has no URL: {server!r}"
        raise ProxyError(msg)
    return endpoint, None


def _uds_transport(url: str, uds: str) -> StreamableHttpTransport:
    """Return a transport that sends the HTTP requests for ``url`` through ``uds``."""
    # Build the same HTTP client as the MCP SDK: httpx2 from SDK 2 (fastmcp 4)
    # on, httpx before.
    if int(version("mcp").split(".")[0]) >= 2:
        import httpx2 as httpx  # noqa: PLC0415
    else:
        import httpx  # noqa: PLC0415

    def client_factory(headers=None, timeout=None, auth=None, **kwargs):
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=uds),
            headers=headers,
            # The MCP SDK's default, with a long read timeout for streams.
            timeout=timeout or httpx.Timeout(30.0, read=300.0),
            auth=auth,
            **kwargs,
        )

    return StreamableHttpTransport(url, httpx_client_factory=client_factory)


async def run_proxy(url: str, uds: str | None = None) -> None:
    """Run a FastMCP proxy to ``url`` over stdio, through ``uds`` if given."""
    proxy = create_proxy(_uds_transport(url, uds) if uds else url)
    await proxy.run_async(transport="stdio", show_banner=False)


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m jupyter_server_mcp.proxy``."""
    # MCP stdio traffic occupies stdout, so keep all logging on stderr.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)

    args = _parse_args(argv)

    try:
        url, uds = resolve_endpoint(args.url, args.uds, args.runtime_dir, args.cwd)
    except ProxyError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    try:
        asyncio.run(run_proxy(url, uds))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
