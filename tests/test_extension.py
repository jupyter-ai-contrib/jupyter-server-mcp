"""Test Jupyter Server extension functionality."""

import asyncio
import importlib.metadata as importlib_metadata
import json
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jupyter_server_mcp import proxy, runtime
from jupyter_server_mcp.extension import MCPExtensionApp, _connect_host
from jupyter_server_mcp.mcp_server import MCPServer


@pytest.fixture(autouse=True)
def _isolated_jupyter_runtime_dir(tmp_path_factory, monkeypatch):
    """Ensure tests never write MCP info files to the real runtime directory.

    Individual tests may still override this by monkey-patching again or by
    installing their own ``runtime_dir`` fixture.
    """
    fake_dir = tmp_path_factory.mktemp("jupyter_runtime_dir")
    monkeypatch.setattr(
        "jupyter_server_mcp.extension.jupyter_runtime_dir",
        lambda: str(fake_dir),
    )


async def _run_until_cancelled():
    """Keep mocked server tasks alive until the extension stops them."""
    await asyncio.Future()


def _mock_running_server(registered_tools=None, host="localhost", port=3001):
    """Create an MCP server mock whose start task stays alive."""
    mock_server = Mock()
    mock_server.start_server = AsyncMock(side_effect=_run_until_cancelled)
    # Mimic the real MCPServer: wait_until_bound resolves immediately so the
    # extension's startup confirmation completes without touching uvicorn.
    mock_server.wait_until_bound = AsyncMock(return_value=None)
    mock_server._registered_tools = [] if registered_tools is None else registered_tools
    mock_server.host = host
    mock_server.port = port
    return mock_server


class TestMCPExtensionApp:
    """Test MCPExtensionApp functionality."""

    def test_extension_creation(self):
        """Test creating extension with defaults."""
        extension = MCPExtensionApp()

        assert extension.name == "jupyter_server_mcp"
        assert extension.mcp_port == 3001
        assert extension.mcp_name == "Jupyter MCP Server"

    def test_extension_trait_configuration(self):
        """Test configuring extension via traits."""
        extension = MCPExtensionApp()

        # Test port configuration
        extension.mcp_port = 3010
        assert extension.mcp_port == 3010

        # Test name configuration
        extension.mcp_name = "My Custom Server"
        assert extension.mcp_name == "My Custom Server"

        # Test tools configuration
        extension.mcp_tools = ["os:getcwd", "math:sqrt"]
        assert extension.mcp_tools == ["os:getcwd", "math:sqrt"]

    def test_initialize_handlers(self):
        """Test handler initialization (should be no-op)."""
        extension = MCPExtensionApp()
        # Should not raise any errors
        extension.initialize_handlers()

    def test_initialize_settings(self):
        """Test settings initialization (should be no-op)."""
        extension = MCPExtensionApp()
        # Should not raise any errors
        extension.initialize_settings()


class TestMCPExtensionLifecycle:
    """Test extension lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_extension_success(self):
        """Test successful extension startup."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3098
        extension.mcp_name = "Test Server"

        # Mock the MCP server creation to avoid actual server startup
        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server()
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            await extension.start_extension()

            # Verify server was created and started
            mock_mcp_class.assert_called_once_with(
                parent=extension,
                name=extension.mcp_name,
                port=extension.mcp_port,
            )
            mock_server.start_server.assert_called_once()

            # Verify extension state
            assert extension.mcp_server_instance == mock_server
            assert extension.mcp_server_task is not None
            extension._confirm_mcp_server_started.assert_awaited_once()

            await extension.stop_extension()

    @pytest.mark.asyncio
    async def test_start_extension_failure(self):
        """Test extension startup failure handling."""
        extension = MCPExtensionApp()

        # Mock server creation to raise an exception
        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_mcp_class.side_effect = Exception("Server creation failed")

            with pytest.raises(Exception, match="Server creation failed"):
                await extension.start_extension()

            assert extension.mcp_server_instance is None
            assert extension.mcp_server_task is None

    @pytest.mark.asyncio
    async def test_start_extension_detects_server_task_failure(self):
        """Test startup failure from the background MCP server task."""
        extension = MCPExtensionApp()

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = Mock()
            mock_server.start_server = AsyncMock(
                side_effect=RuntimeError("Port 3001 is already in use")
            )
            # If start_server raises, the task should finish first; the bound
            # waiter should never resolve on its own.
            mock_server.wait_until_bound = AsyncMock(side_effect=_run_until_cancelled)
            mock_server._registered_tools = []
            mock_mcp_class.return_value = mock_server

            with pytest.raises(RuntimeError, match="Port 3001 is already in use"):
                await extension.start_extension()

            assert extension.mcp_server_instance is None
            assert extension.mcp_server_task is None

    @pytest.mark.asyncio
    async def test_stop_extension_with_running_server(self):
        """Test stopping extension with running server."""
        extension = MCPExtensionApp()

        # Create a real asyncio task that can be cancelled
        async def dummy_task():
            await asyncio.sleep(10)  # Long running task

        task = asyncio.create_task(dummy_task())

        # Set up extension state
        extension.mcp_server_task = task
        extension.mcp_server_instance = Mock()

        await extension.stop_extension()

        # Verify cleanup
        assert task.cancelled()
        assert extension.mcp_server_task is None
        assert extension.mcp_server_instance is None

    @pytest.mark.asyncio
    async def test_stop_extension_uses_mcp_server_shutdown(self):
        """Test stopping extension through the MCP server shutdown hook."""
        extension = MCPExtensionApp()
        stop_event = asyncio.Event()

        async def dummy_task():
            await stop_event.wait()

        async def stop_server():
            stop_event.set()

        task = asyncio.create_task(dummy_task())
        server = MCPServer()
        server.stop_server = AsyncMock(side_effect=stop_server)

        extension.mcp_server_task = task
        extension.mcp_server_instance = server

        await extension.stop_extension()

        server.stop_server.assert_called_once()
        assert task.done()
        assert not task.cancelled()
        assert extension.mcp_server_task is None
        assert extension.mcp_server_instance is None

    @pytest.mark.asyncio
    async def test_stop_extension_no_server(self):
        """Test stopping extension when no server is running."""
        extension = MCPExtensionApp()

        # No server running
        extension.mcp_server_task = None
        extension.mcp_server_instance = None

        # Should not raise any errors
        await extension.stop_extension()

    @pytest.mark.asyncio
    async def test_stop_extension_completed_task(self):
        """Test stopping extension with completed task."""
        extension = MCPExtensionApp()

        # Create a mock completed task
        mock_task = Mock()
        mock_task.done.return_value = True

        extension.mcp_server_task = mock_task
        extension.mcp_server_instance = Mock()

        await extension.stop_extension()

        # Should not try to cancel completed task
        mock_task.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        """Test complete start -> stop lifecycle."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3099
        extension.mcp_name = "Lifecycle Test Server"

        # Mock the MCP server
        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server()
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            # Start extension
            await extension.start_extension()

            # Verify started
            assert extension.mcp_server_instance is not None
            assert extension.mcp_server_task is not None

            # The task should be created by start_extension
            original_task = extension.mcp_server_task

            # Stop extension
            await extension.stop_extension()

            # Verify stopped
            assert extension.mcp_server_instance is None
            assert extension.mcp_server_task is None
            # Task should be either cancelled or done (in mock scenarios,
            # it might finish before cancellation)
            assert original_task.cancelled() or original_task.done()
            extension._confirm_mcp_server_started.assert_awaited_once()


class TestExtensionIntegration:
    """Integration tests for extension."""

    @pytest.mark.integration
    def test_extension_with_real_configuration(self):
        """Test extension with realistic configuration."""
        extension = MCPExtensionApp()

        # Configure like a real deployment
        extension.mcp_port = 3020
        extension.mcp_name = "Production MCP Server"

        # Should initialize without errors
        extension.initialize_handlers()
        extension.initialize_settings()

        # Configuration should be preserved
        assert extension.mcp_port == 3020
        assert extension.mcp_name == "Production MCP Server"


class TestToolLoading:
    """Test tool loading functionality."""

    def test_load_function_from_string_valid(self):
        """Test loading valid functions from string specs."""
        extension = MCPExtensionApp()

        # Test loading os.getcwd
        func = extension._load_function_from_string("os:getcwd")
        assert callable(func)
        assert func.__name__ == "getcwd"

        # Test loading math.sqrt
        func = extension._load_function_from_string("math:sqrt")
        assert callable(func)
        assert func.__name__ == "sqrt"

    def test_load_function_from_string_invalid_format(self):
        """Test loading functions with invalid format."""
        extension = MCPExtensionApp()

        with pytest.raises(ValueError, match="Invalid tool specification"):
            extension._load_function_from_string("invalid_format")

        with pytest.raises(ValueError, match="Invalid tool specification"):
            extension._load_function_from_string("no_colon_here")

    def test_load_function_from_string_invalid_module(self):
        """Test loading functions from non-existent modules."""
        extension = MCPExtensionApp()

        with pytest.raises(ImportError, match="Could not import module"):
            extension._load_function_from_string("nonexistent_module:some_func")

    def test_load_function_from_string_invalid_function(self):
        """Test loading non-existent functions from valid modules."""
        extension = MCPExtensionApp()

        with pytest.raises(AttributeError, match=r"Function.*not found"):
            extension._load_function_from_string("os:nonexistent_function")

    def test_load_function_with_nested_module(self):
        """Test loading functions from nested modules."""
        extension = MCPExtensionApp()

        # Test loading from json.dumps
        func = extension._load_function_from_string("json:dumps")
        assert callable(func)
        assert func.__name__ == "dumps"

    def test_register_configured_tools_empty(self):
        """Test registering tools when mcp_tools is empty."""
        extension = MCPExtensionApp()
        extension.mcp_server_instance = Mock()
        extension.mcp_tools = []

        # Should not call register_tool
        extension._register_tools(extension.mcp_tools, source="configuration")
        extension.mcp_server_instance.register_tool.assert_not_called()

    def test_register_configured_tools_valid(self):
        """Test registering valid configured tools."""
        extension = MCPExtensionApp()
        extension.mcp_server_instance = Mock()
        extension.mcp_tools = ["os:getcwd", "math:sqrt"]

        # Capture log output
        with patch("jupyter_server_mcp.extension.logger") as mock_logger:
            extension._register_tools(extension.mcp_tools, source="configuration")

            # Should register both tools
            assert extension.mcp_server_instance.register_tool.call_count == 2

            # Check log messages
            mock_logger.info.assert_any_call("Registering 2 tools from configuration")
            mock_logger.info.assert_any_call(
                "✅ Registered tool from configuration: os:getcwd"
            )
            mock_logger.info.assert_any_call(
                "✅ Registered tool from configuration: math:sqrt"
            )

    def test_register_configured_tools_with_errors(self):
        """Test registering tools when some fail to load."""
        extension = MCPExtensionApp()
        extension.mcp_server_instance = Mock()
        extension.mcp_tools = ["os:getcwd", "invalid:function", "math:sqrt"]

        with patch("jupyter_server_mcp.extension.logger") as mock_logger:
            extension._register_tools(extension.mcp_tools, source="configuration")

            # Should register 2 valid tools (os:getcwd and math:sqrt)
            assert extension.mcp_server_instance.register_tool.call_count == 2

            # Check error logging
            mock_logger.error.assert_any_call(
                "❌ Failed to register tool 'invalid:function' from configuration: "
                "Could not import module 'invalid': No module named 'invalid'"
            )


class TestExtensionWithTools:
    """Test extension lifecycle with configured tools."""

    @pytest.mark.asyncio
    async def test_start_extension_with_tools(self):
        """Test extension startup with configured tools."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3089
        extension.mcp_name = "Test Server With Tools"
        extension.mcp_tools = ["os:getcwd", "math:sqrt"]
        # Isolate this test from any real entrypoints installed in the env.
        extension.use_tool_discovery = False

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server({"getcwd": {}, "sqrt": {}})
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            await extension.start_extension()

            # Verify server creation
            mock_mcp_class.assert_called_once_with(
                parent=extension, name="Test Server With Tools", port=3089
            )

            # Verify tools were registered
            assert mock_server.register_tool.call_count == 2
            extension._confirm_mcp_server_started.assert_awaited_once()

            await extension.stop_extension()

    @pytest.mark.asyncio
    async def test_start_extension_no_tools(self):
        """Test extension startup with no configured tools."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3088
        extension.mcp_tools = []
        extension.use_tool_discovery = False

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server({})
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            await extension.start_extension()

            # Should not register any tools
            mock_server.register_tool.assert_not_called()
            extension._confirm_mcp_server_started.assert_awaited_once()

            await extension.stop_extension()


class TestEntrypointDiscovery:
    """Test entrypoint discovery functionality."""

    def test_discover_entrypoint_tools_multiple_types(self):
        """Test discovering tools from both list and function entrypoints."""
        extension = MCPExtensionApp()

        # Create mock entrypoints - one list, one function
        mock_ep1 = Mock()
        mock_ep1.name = "package1_tools"
        mock_ep1.value = "package1.tools:TOOLS"
        mock_ep1.load.return_value = ["os:getcwd", "math:sqrt"]

        mock_ep2 = Mock()
        mock_ep2.name = "package2_tools"
        mock_ep2.value = "package2.tools:get_tools"
        mock_function = Mock(return_value=["json:dumps", "time:time"])
        mock_ep2.load.return_value = mock_function

        with patch("importlib.metadata.entry_points") as mock_ep_func:
            mock_ep_func.return_value.select = Mock(return_value=[mock_ep1, mock_ep2])

            tools = extension._discover_entrypoint_tools()
            assert len(tools) == 4
            assert set(tools) == {"os:getcwd", "math:sqrt", "json:dumps", "time:time"}
            mock_function.assert_called_once()  # Function was called

    def test_discover_entrypoint_tools_error_handling(self):
        """Test that discovery handles invalid entrypoints gracefully."""
        extension = MCPExtensionApp()

        # Mix of valid and invalid entrypoints
        valid_ep = Mock()
        valid_ep.name = "valid"
        valid_ep.load.return_value = ["os:getcwd"]

        invalid_type_ep = Mock()
        invalid_type_ep.name = "invalid_type"
        invalid_type_ep.load.return_value = "not_a_list"

        function_bad_return_ep = Mock()
        function_bad_return_ep.name = "bad_function"
        function_bad_return_ep.load.return_value = Mock(return_value={"not": "list"})

        load_error_ep = Mock()
        load_error_ep.name = "load_error"
        load_error_ep.load.side_effect = ImportError("Module not found")

        with patch("importlib.metadata.entry_points") as mock_ep_func:
            mock_ep_func.return_value.select = Mock(
                return_value=[
                    valid_ep,
                    invalid_type_ep,
                    function_bad_return_ep,
                    load_error_ep,
                ]
            )

            with patch("jupyter_server_mcp.extension.logger"):
                tools = extension._discover_entrypoint_tools()
                # Should only get the valid one
                assert tools == ["os:getcwd"]

    def test_discover_entrypoint_tools_disabled(self):
        """Test that discovery returns empty list when disabled."""
        extension = MCPExtensionApp()
        extension.use_tool_discovery = False

        # Should return empty without trying to discover
        tools = extension._discover_entrypoint_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_start_extension_with_entrypoints_and_config(self):
        """Test extension startup with both entrypoint and configured tools."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3086
        extension.use_tool_discovery = True
        extension.mcp_tools = ["json:dumps"]

        discovered_tools = ["os:getcwd"]

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server({"getcwd": {}, "dumps": {}})
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            with patch.object(
                extension, "_discover_entrypoint_tools", return_value=discovered_tools
            ):
                await extension.start_extension()

                # Should register both entrypoint (1) and configured (1) tools = 2 total
                assert mock_server.register_tool.call_count == 2
                extension._confirm_mcp_server_started.assert_awaited_once()

                await extension.stop_extension()


class TestRuntimeInfoPublishing:
    """Test the runtime info file produced by the extension."""

    @pytest.fixture
    def runtime_dir(self, tmp_path, monkeypatch):
        """Redirect ``jupyter_runtime_dir`` so tests do not touch the user's dir."""
        monkeypatch.setattr(
            "jupyter_server_mcp.extension.jupyter_runtime_dir",
            lambda: str(tmp_path),
        )
        return tmp_path

    @pytest.mark.asyncio
    async def test_info_file_written_on_startup_and_removed_on_stop(self, runtime_dir):
        """Startup writes the info file; shutdown removes it."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3080
        extension.mcp_name = "Startup Test"

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server(port=3080)
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            await extension.start_extension()

            info_path = runtime.info_file_path(runtime_dir, os.getpid())
            assert info_path.exists(), "info file should be written on startup"

            data = json.loads(info_path.read_text())
            assert data["pid"] == os.getpid()
            assert data["port"] == 3080
            assert data["url"] == "http://localhost:3080/mcp"
            assert data["name"] == "Startup Test"
            assert data["root_dir"]  # populated from serverapp or cwd

            await extension.stop_extension()

            assert not info_path.exists(), "info file should be removed on stop"

    @pytest.mark.asyncio
    async def test_info_file_cleaned_up_on_startup_failure(self, runtime_dir):
        """When startup fails after the info file is written, clean it up."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3081

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server(port=3081)
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            # Force a failure after the info file would be written.
            with (
                patch.object(
                    extension,
                    "_publish_runtime_info",
                    side_effect=RuntimeError("boom"),
                ),
                pytest.raises(RuntimeError, match="boom"),
            ):
                await extension.start_extension()

        info_path = runtime.info_file_path(runtime_dir, os.getpid())
        assert not info_path.exists()
        assert extension.mcp_server_instance is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("runtime_dir")
    async def test_info_file_publish_is_non_fatal_on_error(self):
        """If publishing fails, the extension should still start."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3082

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server(port=3082)
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            with patch(
                "jupyter_server_mcp.extension.write_info_file",
                side_effect=OSError("disk full"),
            ):
                await extension.start_extension()

            assert extension.mcp_server_instance is mock_server
            assert extension._runtime_info_path is None

            await extension.stop_extension()

    @pytest.mark.asyncio
    async def test_info_file_uses_serverapp_root_dir_when_available(self, runtime_dir):
        """When a serverapp exposes root_dir, it should be recorded verbatim."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3083
        extension.serverapp = Mock(root_dir=str(runtime_dir))

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server(port=3083)
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            await extension.start_extension()

            info_path = runtime.info_file_path(runtime_dir, os.getpid())
            data = json.loads(info_path.read_text())
            assert data["root_dir"] == str(runtime_dir.resolve())

            await extension.stop_extension()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("bind_host", "expected_connect_host"),
        [
            ("0.0.0.0", "127.0.0.1"),
            ("::", "::1"),
            ("localhost", "localhost"),
        ],
    )
    async def test_info_file_url_rewrites_wildcard_hosts(
        self, runtime_dir, bind_host, expected_connect_host
    ):
        """Wildcard bind hosts must be replaced with a dialable loopback in the URL."""
        extension = MCPExtensionApp()
        extension.mcp_port = 3084

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            mock_server = _mock_running_server(host=bind_host, port=3084)
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            await extension.start_extension()

            info_path = runtime.info_file_path(runtime_dir, os.getpid())
            data = json.loads(info_path.read_text())
            assert data["host"] == bind_host
            assert data["url"] == f"http://{expected_connect_host}:3084/mcp"

            await extension.stop_extension()

    def test_detect_root_dir_falls_back_to_cwd_when_serverapp_empty(self, tmp_path):
        """An empty serverapp.root_dir should fall back to the current directory."""
        extension = MCPExtensionApp()
        extension.serverapp = Mock(root_dir="")

        with patch("jupyter_server_mcp.extension.Path.cwd", return_value=tmp_path):
            assert extension._detect_root_dir() == str(tmp_path.resolve())

    @pytest.mark.asyncio
    async def test_info_file_records_ephemeral_port_chosen_by_os(self, runtime_dir):
        """When mcp_port is 0, the info file must record the real bound port."""
        extension = MCPExtensionApp()
        extension.mcp_port = 0  # request an ephemeral port

        with patch("jupyter_server_mcp.extension.MCPServer") as mock_mcp_class:
            # Simulate uvicorn binding to port 54321 after startup.
            mock_server = _mock_running_server(port=54321)
            mock_mcp_class.return_value = mock_server
            extension._confirm_mcp_server_started = AsyncMock()

            await extension.start_extension()

            info_path = runtime.info_file_path(runtime_dir, os.getpid())
            data = json.loads(info_path.read_text())
            assert data["port"] == 54321
            assert data["url"] == "http://localhost:54321/mcp"

            await extension.stop_extension()


class TestConfirmMCPServerStarted:
    """Tests for the bind/failure/timeout logic in ``_confirm_mcp_server_started``."""

    @pytest.mark.asyncio
    async def test_returns_when_wait_until_bound_resolves(self):
        """Normal path: ``wait_until_bound`` resolves first, no exception."""
        extension = MCPExtensionApp()
        extension.mcp_startup_timeout = 1

        mock_server = _mock_running_server()
        task = asyncio.create_task(_run_until_cancelled())
        extension.mcp_server_instance = mock_server
        extension.mcp_server_task = task

        try:
            await extension._confirm_mcp_server_started()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_raises_when_server_task_fails_before_bind(self):
        """If start_server raises before bind, the exception is surfaced."""
        extension = MCPExtensionApp()
        extension.mcp_startup_timeout = 1

        mock_server = Mock()
        mock_server.wait_until_bound = AsyncMock(side_effect=_run_until_cancelled)

        async def failing_start():
            msg = "bind failed"
            raise RuntimeError(msg)

        task = asyncio.create_task(failing_start())
        extension.mcp_server_instance = mock_server
        extension.mcp_server_task = task

        with pytest.raises(RuntimeError, match="bind failed"):
            await extension._confirm_mcp_server_started()

    @pytest.mark.asyncio
    async def test_raises_timeout_when_bind_stalls(self):
        """If neither bind nor failure happens in time, raise ``TimeoutError``."""
        extension = MCPExtensionApp()
        extension.mcp_startup_timeout = 0.05

        mock_server = Mock()
        mock_server.wait_until_bound = AsyncMock(side_effect=_run_until_cancelled)

        task = asyncio.create_task(_run_until_cancelled())
        extension.mcp_server_instance = mock_server
        extension.mcp_server_task = task

        try:
            with pytest.raises(TimeoutError, match="did not bind"):
                await extension._confirm_mcp_server_started()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


class TestConsoleScript:
    """Verify the ``jupyter-server-mcp-proxy`` console script is installed."""

    def test_proxy_entry_point_is_registered(self):
        """The console script should resolve to ``proxy.main``."""
        entry_points = importlib_metadata.entry_points()
        if hasattr(entry_points, "select"):
            scripts = entry_points.select(group="console_scripts")
        else:
            scripts = entry_points.get("console_scripts", [])

        by_name = {ep.name: ep for ep in scripts}
        ep = by_name.get("jupyter-server-mcp-proxy")
        assert ep is not None, (
            "jupyter-server-mcp-proxy console script is not installed"
        )
        assert ep.load() is proxy.main


class TestConnectHost:
    """Unit tests for ``_connect_host`` wildcard normalization."""

    @pytest.mark.parametrize(
        ("bind_host", "expected"),
        [
            ("0.0.0.0", "127.0.0.1"),
            ("", "127.0.0.1"),
            ("::", "::1"),
            ("::0", "::1"),
            ("localhost", "localhost"),
            ("192.168.1.10", "192.168.1.10"),
            ("example.com", "example.com"),
        ],
    )
    def test_connect_host_mapping(self, bind_host, expected):
        assert _connect_host(bind_host) == expected
