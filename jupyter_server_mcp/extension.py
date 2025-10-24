"""Jupyter Server extension for managing MCP server."""

import asyncio
import contextlib
import importlib
import importlib.metadata
import logging

from jupyter_server.extension.application import ExtensionApp
from traitlets import Bool, Int, List, Unicode

from .mcp_server import MCPServer

logger = logging.getLogger(__name__)


class MCPExtensionApp(ExtensionApp):
    """The Jupyter Server MCP extension app."""

    name = "jupyter_server_mcp"
    description = "Jupyter Server extension providing MCP server for tool registration"

    # Configurable traits
    mcp_port = Int(default_value=3001, help="Port for the MCP server to listen on").tag(
        config=True
    )

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

    async def start_extension(self):
        """Start the extension - called after Jupyter Server starts."""
        try:
            self.log.info(
                f"Starting MCP server '{self.mcp_name}' on port {self.mcp_port}"
            )

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

            # Give the server a moment to start
            await asyncio.sleep(0.5)

            registered_count = len(self.mcp_server_instance._registered_tools)
            self.log.info(f"✅ MCP server started on port {self.mcp_port}")
            self.log.info(f"Total registered tools: {registered_count}")

        except Exception as e:
            self.log.error(f"Failed to start MCP server: {e}")
            raise

    async def _start_jupyter_server_extension(self, serverapp):  # noqa: ARG002
        """Start the extension - called after Jupyter Server starts."""
        await self.start_extension()

    async def stop_extension(self):
        """Stop the extension - called when Jupyter Server shuts down."""
        if self.mcp_server_task and not self.mcp_server_task.done():
            self.log.info("Stopping MCP server")
            self.mcp_server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.mcp_server_task

        # Always clean up
        self.mcp_server_task = None
        self.mcp_server_instance = None
        self.log.info("MCP server stopped")
