"""Tests for the stdio proxy discovery and CLI logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jupyter_server_mcp import proxy, runtime


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


class TestResolveUrl:
    """Tests for ``resolve_url``."""

    def test_explicit_url_wins(self, isolated_runtime_dir):
        """An explicit URL short-circuits discovery."""
        url = proxy.resolve_url(
            url="http://explicit:9999/mcp",
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(isolated_runtime_dir),
        )
        assert url == "http://explicit:9999/mcp"

    def test_env_var_wins(self, isolated_runtime_dir, monkeypatch):
        """The env var is honored when no explicit URL is passed."""
        monkeypatch.setenv(proxy.ENV_URL, "http://env:1234/mcp")
        url = proxy.resolve_url(
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(isolated_runtime_dir),
        )
        assert url == "http://env:1234/mcp"

    def test_single_server_discovery(self, isolated_runtime_dir):
        """A single running server is selected automatically."""
        _publish_server(isolated_runtime_dir, 101, root_dir=isolated_runtime_dir)

        url = proxy.resolve_url(
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(isolated_runtime_dir),
        )

        assert url == "http://localhost:3001/mcp"

    def test_no_servers_raises(self, isolated_runtime_dir):
        """An empty runtime directory produces a helpful error."""
        with pytest.raises(proxy.ProxyError, match="No running Jupyter MCP servers"):
            proxy.resolve_url(
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
            proxy.resolve_url(
                runtime_dir=str(isolated_runtime_dir),
                cwd=str(isolated_runtime_dir),
            )

    def test_explicit_url_without_scheme_rejected(self, isolated_runtime_dir):
        """URLs without a scheme are rejected before any discovery happens."""
        with pytest.raises(proxy.ProxyError, match="absolute http"):
            proxy.resolve_url(
                url="localhost:3001/mcp",
                runtime_dir=str(isolated_runtime_dir),
                cwd=str(isolated_runtime_dir),
            )

    def test_explicit_url_with_bad_scheme_rejected(self, isolated_runtime_dir):
        """Non-http(s) schemes (e.g. file://) are rejected with a scheme error."""
        with pytest.raises(proxy.ProxyError, match="http or https"):
            proxy.resolve_url(
                url="file:///etc/passwd",
                runtime_dir=str(isolated_runtime_dir),
                cwd=str(isolated_runtime_dir),
            )

    def test_env_url_with_bad_scheme_rejected(self, isolated_runtime_dir, monkeypatch):
        """Validation applies to the env-var form too, not only to ``--url``."""
        monkeypatch.setenv(proxy.ENV_URL, "ftp://example.com/mcp")
        with pytest.raises(proxy.ProxyError, match="http or https"):
            proxy.resolve_url(
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

        url_a = proxy.resolve_url(
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(root_a),
        )
        url_b = proxy.resolve_url(
            runtime_dir=str(isolated_runtime_dir),
            cwd=str(root_b),
        )

        assert url_a == "http://localhost:3101/mcp"
        assert url_b == "http://localhost:3102/mcp"


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


class TestMatchScore:
    """Unit tests for ``_match_score``."""

    @pytest.mark.parametrize("bad_root", [None, "", 42, []])
    def test_invalid_root_types_return_none(self, bad_root, tmp_path):
        assert proxy._match_score(bad_root, tmp_path) is None

    def test_unresolvable_root_returns_none(self, tmp_path, monkeypatch):
        """If ``Path.resolve`` raises OSError, we must not score that server."""

        original_resolve = Path.resolve

        def raise_os_error(self, *args, **kwargs):  # noqa: ARG001
            msg = "path loop"
            raise OSError(msg)

        monkeypatch.setattr(Path, "resolve", raise_os_error)
        try:
            assert proxy._match_score("/some/path", tmp_path) is None
        finally:
            monkeypatch.setattr(Path, "resolve", original_resolve)

    def test_descendant_cwd_scores_by_parts(self, tmp_path):
        """The score should be the number of parts in the resolved root path."""
        root = tmp_path / "projects" / "alpha"
        root.mkdir(parents=True)
        cwd = root / "src"
        cwd.mkdir()

        score = proxy._match_score(str(root), cwd.resolve())

        assert score == len(root.resolve().parts)

    def test_unrelated_cwd_returns_none(self, tmp_path):
        root = tmp_path / "projects"
        other = tmp_path / "elsewhere"
        root.mkdir()
        other.mkdir()

        assert proxy._match_score(str(root), other.resolve()) is None


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

        async def fake_run_proxy(url):
            calls.append(url)

        monkeypatch.setattr(proxy, "run_proxy", fake_run_proxy)

        assert proxy.main(["--url", "http://explicit:1/mcp"]) == 0
        assert calls == ["http://explicit:1/mcp"]

    def test_main_treats_keyboard_interrupt_as_success(self, monkeypatch):
        """KeyboardInterrupt while proxying should be a clean shutdown."""

        async def raise_kbi(_url):
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
