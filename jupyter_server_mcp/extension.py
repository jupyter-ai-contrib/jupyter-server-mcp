"""Jupyter Server extension for managing MCP server."""

import asyncio
import contextlib
import importlib
import importlib.metadata
import inspect
import logging
import os
from pathlib import Path

from jupyter_core.paths import jupyter_runtime_dir
from jupyter_server.extension.application import ExtensionApp
from traitlets import Bool, Int, List, Unicode

from .mcp_server import MCPServer
from .runtime import info_file_path, remove_info_file, write_info_file

logger = logging.getLogger(__name__)

# Wildcard bind addresses are valid for listening but cannot be dialed.
_WILDCARD_CONNECT_HOSTS = {
    "0.0.0.0": "127.0.0.1",
    "": "127.0.0.1",
    "::": "::1",
    "::0": "::1",
}


def _connect_host(bind_host: str) -> str:
    """Return a host usable in a client URL for ``bind_host``.

    Bind addresses like ``0.0.0.0`` or ``::`` instruct a server to listen on
    all interfaces, but clients cannot connect to those literal values. Map
    them to the matching loopback address so the published URL is dialable.
    """
    return _WILDCARD_CONNECT_HOSTS.get(bind_host, bind_host)


class MCPExtensionApp(ExtensionApp):
    """The Jupyter Server MCP extension app."""

    name = "jupyter_server_mcp"
    description = "Jupyter Server extension providing MCP server for tool registration"

    # Configurable traits
    mcp_port = Int(
        default_value=3001,
        help=(
            "Port for the MCP server to listen on. "
            "Defaults to 3001. Set to 0 to ask the OS to pick a free port — "
            "useful when running multiple Jupyter servers side by side. "
            "When port 0 is used, the stdio proxy "
            "(python -m jupyter_server_mcp.proxy) can auto-discover the "
            "chosen port via the runtime info file."
        ),
    ).tag(config=True)

    mcp_name = Unicode(
        default_value="Jupyter MCP Server", help="Name for the MCP server"
    ).tag(config=True)

    mcp_tools = List(
        trait=Unicode(),
        default_value=[],
        help=(
            "List of tools to register with the MCP server. "
            "Format: 'module_path:function_name' "
            "(e.g., 'os:getcwd', 'math:sqrt')"
        ),
    ).tag(config=True)

    use_tool_discovery = Bool(
        default_value=True,
        help=(
            "Whether to automatically discover and register tools from "
            "Python entrypoints in the 'jupyter_server_mcp.tools' group"
        ),
    ).tag(config=True)

    mcp_server_instance: object | None = None
    mcp_server_task: asyncio.Task | None = None
    mcp_shutdown_timeout = 5
    mcp_startup_timeout = 10
    _runtime_info_path: Path | None = None

    def _load_function_from_string(self, tool_spec: str):
        """Load a function from a string specification.

        Args:
            tool_spec: Function specification in format
                'module_path:function_name'

        Returns:
            The loaded function object

        Raises:
            ValueError: If tool_spec format is invalid
            ImportError: If module cannot be imported
            AttributeError: If function not found in module
        """
        if ":" not in tool_spec:
            msg = (
                f"Invalid tool specification '{tool_spec}'. "
                f"Expected format: 'module_path:function_name'"
            )
            raise ValueError(msg)

        module_path, function_name = tool_spec.rsplit(":", 1)

        try:
            module = importlib.import_module(module_path)
            return getattr(module, function_name)
        except ImportError as e:
            msg = f"Could not import module '{module_path}': {e}"
            raise ImportError(msg) from e
        except AttributeError as e:
            msg = f"Function '{function_name}' not found in module '{module_path}': {e}"
            raise AttributeError(msg) from e

    def _register_tools(self, tool_specs: list[str], source: str = "configuration"):
        """Register tools from a list of tool specifications.

        Args:
            tool_specs: List of tool specifications in 'module:function' format
            source: Description of where tools came from (for logging)
        """
        if not tool_specs:
            return

        logger.info(f"Registering {len(tool_specs)} tools from {source}")

        for tool_spec in tool_specs:
            try:
                function = self._load_function_from_string(tool_spec)
                self.mcp_server_instance.register_tool(function)
                logger.info(f"✅ Registered tool from {source}: {tool_spec}")
            except Exception as e:
                logger.error(
                    f"❌ Failed to register tool '{tool_spec}' from {source}: {e}"
                )
                continue

    def _discover_entrypoint_tools(self) -> list[str]:
        """Discover tools from Python entrypoints in the 'jupyter_server_mcp.tools' group.

        Returns:
            List of tool specifications in 'module:function' format
        """
        if not self.use_tool_discovery:
            return []

        discovered_tools = []

        try:
            # Use importlib.metadata to discover entrypoints
            entrypoints = importlib.metadata.entry_points()

            # Handle both Python 3.10+ and 3.9 style entrypoint APIs
            if hasattr(entrypoints, "select"):
                tools_group = entrypoints.select(group="jupyter_server_mcp.tools")
            else:
                tools_group = entrypoints.get("jupyter_server_mcp.tools", [])

            for entry_point in tools_group:
                try:
                    # Load the entrypoint value (can be a list or a function that returns a list)
                    loaded_value = entry_point.load()

                    # Get tool specs from either a list or callable
                    if isinstance(loaded_value, list):
                        tool_specs = loaded_value
                    elif callable(loaded_value):
                        tool_specs = loaded_value()
                        if not isinstance(tool_specs, list):
                            logger.warning(
                                f"Entrypoint '{entry_point.name}' function returned "
                                f"{type(tool_specs).__name__} instead of list, skipping"
                            )
                            continue
                    else:
                        logger.warning(
                            f"Entrypoint '{entry_point.name}' is neither a list nor callable, skipping"
                        )
                        continue

                    # Validate and collect tool specs
                    valid_specs = [spec for spec in tool_specs if isinstance(spec, str)]
                    invalid_count = len(tool_specs) - len(valid_specs)

                    if invalid_count > 0:
                        logger.warning(
                            f"Skipped {invalid_count} non-string tool specs from '{entry_point.name}'"
                        )

                    discovered_tools.extend(valid_specs)
                    logger.info(
                        f"Discovered {len(valid_specs)} tools from entrypoint '{entry_point.name}'"
                    )

                except Exception as e:
                    logger.error(f"Failed to load entrypoint '{entry_point.name}': {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to discover entrypoints: {e}")

        if not discovered_tools:
            logger.info("No tools discovered from entrypoints")

        return discovered_tools

    def initialize(self):
        """Initialize the extension."""
        super().initialize()
        # serverapp will be available as self.serverapp after parent initialization

    def initialize_handlers(self):
        """Initialize the handlers for the extension."""
        # No HTTP handlers needed - MCP server runs on separate port

    def initialize_settings(self):
        """Initialize settings for the extension."""
        # Configuration is handled by traitlets

    async def _confirm_mcp_server_started(self):
        """Wait for the MCP server to bind its port, or raise if startup fails."""
        task = self.mcp_server_task
        instance = self.mcp_server_instance
        if task is None or instance is None:
            return

        bound_wait = asyncio.ensure_future(instance.wait_until_bound())
        try:
            done, _ = await asyncio.wait(
                {task, bound_wait},
                timeout=self.mcp_startup_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not bound_wait.done():
                bound_wait.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bound_wait

        if task in done:
            # The server task finished before binding — surface its exception.
            await task
            msg = "MCP server exited during startup"
            raise RuntimeError(msg)

        if bound_wait not in done:
            msg = f"MCP server did not bind within {self.mcp_startup_timeout} seconds"
            raise TimeoutError(msg)

    async def _stop_mcp_server_task(self):
        """Stop the MCP server through its own shutdown path, then fall back."""
        if self.mcp_server_task is None or self.mcp_server_task.done():
            return

        self.log.info("Stopping MCP server")

        instance = self.mcp_server_instance
        stop_server = getattr(instance, "stop_server", None)
        if inspect.iscoroutinefunction(stop_server):
            await stop_server()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self.mcp_server_task),
                    timeout=self.mcp_shutdown_timeout,
                )
                return
            except TimeoutError:
                self.log.warning("Timed out waiting for MCP server to stop")

        self.mcp_server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.mcp_server_task

    async def start_extension(self):
        """Start the extension - called after Jupyter Server starts."""
        try:
            port_desc = (
                "an ephemeral port" if self.mcp_port == 0 else f"port {self.mcp_port}"
            )
            self.log.info(f"Starting MCP server '{self.mcp_name}' on {port_desc}")

            self.mcp_server_instance = MCPServer(
                parent=self, name=self.mcp_name, port=self.mcp_port
            )

            # Register tools from entrypoints, then from configuration
            entrypoint_tools = self._discover_entrypoint_tools()
            self._register_tools(entrypoint_tools, source="entrypoints")
            self._register_tools(self.mcp_tools, source="configuration")

            # Start the MCP server in a background task
            self.mcp_server_task = asyncio.create_task(
                self.mcp_server_instance.start_server()
            )

            await self._confirm_mcp_server_started()

            bound_port = getattr(self.mcp_server_instance, "port", self.mcp_port)
            registered_count = len(self.mcp_server_instance._registered_tools)
            self.log.info(f"✅ MCP server started on port {bound_port}")
            self.log.info(f"Total registered tools: {registered_count}")

            self._publish_runtime_info()

        except Exception as e:
            if self.mcp_server_task and not self.mcp_server_task.done():
                self.mcp_server_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.mcp_server_task

            self._clear_runtime_info()
            self.mcp_server_task = None
            self.mcp_server_instance = None
            self.log.error(f"Failed to start MCP server: {e}")
            raise

    def _publish_runtime_info(self):
        """Write a runtime info file so the stdio proxy can discover this server."""
        try:
            server = self.mcp_server_instance
            bind_host = getattr(server, "host", "localhost") or "localhost"
            port = getattr(server, "port", self.mcp_port)
            pid = os.getpid()
            path = info_file_path(jupyter_runtime_dir(), pid)
            # The bind host can be a wildcard like "0.0.0.0" or "::" — those
            # are valid bind addresses but not usable as connect targets.
            url_host = _connect_host(bind_host)
            info = {
                "pid": pid,
                "host": bind_host,
                "port": port,
                "url": f"http://{url_host}:{port}/mcp",
                "name": self.mcp_name,
                "root_dir": self._detect_root_dir(),
            }
            write_info_file(path, info)
        except Exception as exc:
            self.log.warning(f"Could not publish MCP runtime info: {exc}")
            self._runtime_info_path = None
            return

        self._runtime_info_path = path
        self.log.info(f"Wrote MCP runtime info file: {path}")

    def _clear_runtime_info(self):
        """Remove the runtime info file if one was written."""
        path = self._runtime_info_path
        if path is None:
            return
        try:
            remove_info_file(path)
        except OSError as exc:
            self.log.warning(f"Could not remove MCP runtime info file {path}: {exc}")
        finally:
            self._runtime_info_path = None

    def _detect_root_dir(self) -> str:
        """Return the Jupyter server's root directory, falling back to CWD."""
        serverapp = getattr(self, "serverapp", None)
        root_dir = getattr(serverapp, "root_dir", None)
        if root_dir:
            return str(Path(root_dir).resolve())
        return str(Path.cwd().resolve())

    async def _start_jupyter_server_extension(self, serverapp):  # noqa: ARG002
        """Start the extension - called after Jupyter Server starts."""
        await self.start_extension()

    async def stop_extension(self):
        """Stop the extension - called when Jupyter Server shuts down."""
        try:
            await self._stop_mcp_server_task()
        finally:
            self._clear_runtime_info()
            self.mcp_server_task = None
            self.mcp_server_instance = None
        self.log.info("MCP server stopped")
