"""Tests for the stdio proxy discovery and CLI logic."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from jupyter_server_mcp import proxy, runtime
from jupyter_server_mcp.mcp_server import MCPServer


@pytest.fixture
def isolated_runtime_dir(tmp_path, monkeypatch):
    """Point discovery at a temporary runtime directory with live pids."""
    monkeypatch.setattr(runtime, "check_pid", lambda _pid: True)
    monkeypatch.delenv(proxy.ENV_URL, raising=False)
    return tmp_path


def _publish_server(
    runtime_dir: Path,
    pid: int,
    *,
    root_dir: Path | str,
    port: int = 3001,
) -> None:
    """Write an MCP info file in ``runtime_dir`` for ``pid``."""
    path = runtime.info_file_path(runtime_dir, pid)
    runtime.write_info_file(
        path,
        {
            "pid": pid,
            "host": "localhost",
            "port": port,
            "url": f"http://localhost:{port}/mcp",
            "name": f"Jupyter MCP Server {pid}",
            "root_dir": str(root_dir),
        },
    )


class TestResolveEndpoint:
    """Tests for ``resolve_endpoint``."""

    def test_explicit_url_wins(self, isolated_runtime_dir):
        """An explicit URL short-circuits discovery."""
        endpoint = proxy.resolve_endpoint(
            url="http://explicit:9999/mcp",
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(isolated_runtime_dir),
        )
        assert endpoint == ("http://explicit:9999/mcp", None)

    def test_env_var_wins(self, isolated_runtime_dir, monkeypatch):
        """The env var is honored when no explicit URL is passed."""
        monkeypatch.setenv(proxy.ENV_URL, "http://env:1234/mcp")
        endpoint = proxy.resolve_endpoint(
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(isolated_runtime_dir),
        )
        assert endpoint == ("http://env:1234/mcp", None)

    def test_single_server_discovery(self, isolated_runtime_dir):
        """A single running server is selected automatically."""
        _publish_server(isolated_runtime_dir, 101, root_dir=isolated_runtime_dir)

        endpoint = proxy.resolve_endpoint(
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(isolated_runtime_dir),
        )

        assert endpoint == ("http://localhost:3001/mcp", None)

    def test_explicit_uds_uses_localhost_url(self, isolated_runtime_dir):
        """``--uds`` short-circuits discovery and needs no URL."""
        endpoint = proxy.resolve_endpoint(
            uds="/run/mcp.sock",
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(isolated_runtime_dir),
        )
        assert endpoint == (proxy.UDS_URL, "/run/mcp.sock")

    def test_unix_socket_server_discovery(self, isolated_runtime_dir):
        """A discovered server on a Unix socket is reached through it."""
        runtime.write_info_file(
            runtime.info_file_path(isolated_runtime_dir, 301),
            {
                "pid": 301,
                "uds": "/run/mcp.sock",
                "root_dir": str(isolated_runtime_dir),
            },
        )

        endpoint = proxy.resolve_endpoint(
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(isolated_runtime_dir),
        )

        assert endpoint == (proxy.UDS_URL, "/run/mcp.sock")

    def test_no_servers_raises(self, isolated_runtime_dir):
        """An empty runtime directory produces a helpful error."""
        with pytest.raises(proxy.ProxyError, match="No running Jupyter MCP servers"):
            proxy.resolve_endpoint(
                runtime_dir=str(isolated_runtime_dir),
                cwd=str(isolated_runtime_dir),
            )

    def test_discovered_url_missing_raises(self, isolated_runtime_dir):
        """A malformed info dict raises a clear error."""
        path = runtime.info_file_path(isolated_runtime_dir, 17)
        runtime.write_info_file(
            path,
            {"pid": 17, "host": "localhost", "port": 3001, "root_dir": "/tmp"},
        )

        with pytest.raises(proxy.ProxyError, match="has no URL"):
            proxy.resolve_endpoint(
                runtime_dir=str(isolated_runtime_dir),
                cwd=str(isolated_runtime_dir),
            )

    @pytest.mark.parametrize(
        ("source", "value", "match"),
        [
            ("arg", "localhost:3001/mcp", "absolute http"),
            ("arg", "file:///etc/passwd", "http or https"),
            ("env", "ftp://example.com/mcp", "http or https"),
        ],
    )
    def test_invalid_urls_are_rejected(
        self, isolated_runtime_dir, monkeypatch, source, value, match
    ):
        """Explicit and env-var URLs must be absolute http(s) URLs."""
        if source == "env":
            monkeypatch.setenv(proxy.ENV_URL, value)
            url = None
        else:
            url = value

        with pytest.raises(proxy.ProxyError, match=match):
            proxy.resolve_endpoint(
                url=url,
                runtime_dir=str(isolated_runtime_dir),
                cwd=str(isolated_runtime_dir),
            )

    def test_cwd_argument_steers_selection(self, isolated_runtime_dir, tmp_path):
        """Passing ``cwd`` must disambiguate between multiple running servers."""
        root_a = tmp_path / "alpha"
        root_b = tmp_path / "beta"
        root_a.mkdir()
        root_b.mkdir()
        _publish_server(isolated_runtime_dir, 201, root_dir=root_a, port=3101)
        _publish_server(isolated_runtime_dir, 202, root_dir=root_b, port=3102)

        endpoint_a = proxy.resolve_endpoint(
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(root_a),
        )
        endpoint_b = proxy.resolve_endpoint(
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(root_b),
        )

        assert endpoint_a == ("http://localhost:3101/mcp", None)
        assert endpoint_b == ("http://localhost:3102/mcp", None)


class TestSelectServer:
    """Tests for the server selection logic."""

    def test_picks_ancestor_with_highest_specificity(self, tmp_path):
        """Deeper ancestor root_dirs should win over shallower ones."""
        shallow = tmp_path / "projects"
        deep = shallow / "alpha"
        deep.mkdir(parents=True)
        cwd = deep / "src"
        cwd.mkdir()

        servers = [
            {"pid": 1, "url": "http://localhost:3001/mcp", "root_dir": str(shallow)},
            {"pid": 2, "url": "http://localhost:3002/mcp", "root_dir": str(deep)},
        ]

        chosen = proxy.select_server(servers, cwd.resolve())

        assert chosen["pid"] == 2

    def test_falls_back_when_no_match_is_ambiguous(self, tmp_path):
        """If no server contains the cwd, the user must disambiguate."""
        other_a = tmp_path / "a"
        other_b = tmp_path / "b"
        other_a.mkdir()
        other_b.mkdir()
        cwd = tmp_path / "c"
        cwd.mkdir()

        servers = [
            {"pid": 1, "url": "http://localhost:3001/mcp", "root_dir": str(other_a)},
            {"pid": 2, "url": "http://localhost:3002/mcp", "root_dir": str(other_b)},
        ]

        with pytest.raises(proxy.ProxyError, match="Multiple Jupyter MCP servers"):
            proxy.select_server(servers, cwd.resolve())

    def test_ambiguous_same_root_dir(self, tmp_path):
        """Two servers with the same root_dir produce a disambiguation error."""
        root = tmp_path / "shared"
        root.mkdir()

        servers = [
            {"pid": 1, "url": "http://localhost:3001/mcp", "root_dir": str(root)},
            {"pid": 2, "url": "http://localhost:3002/mcp", "root_dir": str(root)},
        ]

        with pytest.raises(proxy.ProxyError, match="Multiple Jupyter MCP servers"):
            proxy.select_server(servers, root.resolve())

    def test_single_server_returned_regardless_of_cwd(self, tmp_path):
        """With only one candidate, return it even if cwd is unrelated."""
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()

        servers = [
            {"pid": 1, "url": "http://localhost:3001/mcp", "root_dir": str(tmp_path)},
        ]

        assert proxy.select_server(servers, unrelated.resolve())["pid"] == 1


class TestMainCLI:
    """Tests for the ``main`` CLI entry point."""

    def test_main_exits_cleanly_on_proxy_error(
        self, isolated_runtime_dir, capsys, monkeypatch
    ):
        """Discovery failures produce an error message and non-zero exit."""
        monkeypatch.chdir(isolated_runtime_dir)
        exit_code = proxy.main([])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert "No running Jupyter MCP servers" in captured.err

    def test_main_connects_with_explicit_url(self, monkeypatch):
        """An explicit URL should trigger ``run_proxy`` without discovery."""
        calls = []

        async def fake_run_proxy(url, uds=None):
            calls.append((url, uds))

        monkeypatch.setattr(proxy, "run_proxy", fake_run_proxy)

        assert proxy.main(["--url", "http://explicit:1/mcp"]) == 0
        assert calls == [("http://explicit:1/mcp", None)]

    def test_main_connects_with_explicit_uds(self, monkeypatch):
        """``--uds`` should be passed through to ``run_proxy``."""
        calls = []

        async def fake_run_proxy(url, uds=None):
            calls.append((url, uds))

        monkeypatch.setattr(proxy, "run_proxy", fake_run_proxy)

        assert proxy.main(["--uds", "/run/mcp.sock"]) == 0
        assert calls == [(proxy.UDS_URL, "/run/mcp.sock")]

    def test_main_treats_keyboard_interrupt_as_success(self, monkeypatch):
        """KeyboardInterrupt while proxying should be a clean shutdown."""

        async def raise_kbi(_url, _uds=None):
            raise KeyboardInterrupt

        monkeypatch.setattr(proxy, "run_proxy", raise_kbi)

        assert proxy.main(["--url", "http://x/mcp"]) == 0


class TestRunProxy:
    """Test that ``run_proxy`` configures FastMCP correctly."""

    @pytest.mark.asyncio
    async def test_run_proxy_uses_stdio_transport(self):
        """``run_proxy`` should forward the URL and pick the stdio transport."""
        fake_proxy = AsyncMock()

        with patch(
            "jupyter_server_mcp.proxy.create_proxy", return_value=fake_proxy
        ) as create:
            await proxy.run_proxy("http://localhost:3001/mcp")

        create.assert_called_once_with("http://localhost:3001/mcp")
        fake_proxy.run_async.assert_awaited_once_with(
            transport="stdio", show_banner=False
        )

    @pytest.mark.asyncio
    async def test_run_proxy_connects_through_uds(self):
        """With a socket, ``run_proxy`` should proxy a UDS-backed transport."""
        fake_proxy = AsyncMock()

        with patch(
            "jupyter_server_mcp.proxy.create_proxy", return_value=fake_proxy
        ) as create:
            await proxy.run_proxy(proxy.UDS_URL, "/run/mcp.sock")

        (transport,), _ = create.call_args
        assert isinstance(transport, StreamableHttpTransport)
        assert transport.url == proxy.UDS_URL

    def test_uds_client_keeps_mcp_default_timeout(self):
        """The UDS client must not fall back to httpx's 5 second read timeout."""
        transport = proxy._uds_transport(proxy.UDS_URL, "/run/mcp.sock")

        client = transport.httpx_client_factory(
            headers=None, auth=None, follow_redirects=True
        )

        assert client.timeout.read == 300.0


def add(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y


@pytest.mark.integration
@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")
@pytest.mark.asyncio
async def test_uds_transport_calls_server_tools(socket_dir):
    """The proxy's UDS transport reaches a real server's tools."""
    path = str(socket_dir / "mcp.sock")
    server = MCPServer(uds=path)
    server.register_tool(add)
    task = asyncio.create_task(server.start_server())
    try:
        await server.wait_until_bound(timeout=5.0)
        async with Client(proxy._uds_transport(proxy.UDS_URL, path)) as client:
            result = await client.call_tool("add", {"x": 2, "y": 3})
        assert result.data == 5
    finally:
        await server.stop_server()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5.0)
