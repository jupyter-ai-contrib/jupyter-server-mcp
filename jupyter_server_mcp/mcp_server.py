"""Simple MCP server for registering Python functions as tools."""

import inspect
import json
import logging
from collections.abc import Callable
from functools import wraps
from inspect import iscoroutinefunction, signature
from typing import Any, Union, get_args, get_origin

from fastmcp import FastMCP
from traitlets import Int, Unicode
from traitlets.config.configurable import LoggingConfigurable

logger = logging.getLogger(__name__)


def _is_dict_compatible_annotation(annotation) -> bool:
    """Check if an annotation expects dict values that can be JSON-converted."""
    # Direct dict annotation
    if annotation is dict:
        return True

    # Union types: Optional[dict], Union[dict, None], dict | None
    origin = get_origin(annotation)
    if origin is Union or (
        hasattr(annotation, "__class__")
        and annotation.__class__.__name__ == "UnionType"
    ):
        args = get_args(annotation)
        return dict in args

    # Typed dict annotations: Dict[K, V], dict[str, Any]
    return bool(hasattr(annotation, "__origin__") and annotation.__origin__ is dict)


def _wrap_with_json_conversion(func: Callable) -> Callable:
    """
    Wrapper that automatically converts JSON string arguments to dictionaries.

    This addresses the common issue where MCP clients pass dictionary arguments
    as JSON strings instead of structured objects. The wrapper inspects the
    function signature and attempts JSON parsing for parameters annotated as
    dict types when they are received as strings.

    Additionally, this function modifies the type annotations to accept Union[dict, str]
    for dict parameters to allow Pydantic validation to pass.

    This conversion is always applied to all registered tools to ensure compatibility
    with various MCP clients that may serialize dict parameters differently.

    Args:
        func: The function to wrap

    Returns:
        Wrapped function that handles JSON string conversion with modified annotations
    """
    sig = signature(func)

    def _should_convert_to_dict(annotation, value):
        """Check if a parameter should be converted from JSON string to dict."""
        return isinstance(value, str) and _is_dict_compatible_annotation(annotation)

    def _add_string_to_annotation(annotation):
        """Modify annotation to also accept strings for dict types."""
        # Direct dict annotation -> dict | str
        if annotation is dict:
            return dict | str

        # Union types: add str to existing union
        origin = get_origin(annotation)
        if origin is Union:
            args = get_args(annotation)
            if dict in args and str not in args:
                return Union[(*tuple(args), str)]
            return annotation

        # New Python 3.10+ union syntax: dict | None
        if (
            hasattr(annotation, "__class__")
            and annotation.__class__.__name__ == "UnionType"
        ):
            args = get_args(annotation)
            if dict in args and str not in args:
                # Reconstruct the union with str added
                new_args = (*tuple(args), str)
                # Create new union type
                result = new_args[0]
                for arg in new_args[1:]:
                    result = result | arg
                return result
            return annotation

        # Typed dict annotations -> annotation | str
        if hasattr(annotation, "__origin__") and annotation.__origin__ is dict:
            return annotation | str

        return annotation

    # Create new annotations that accept strings for dict parameters
    new_annotations = {}
    for param_name, param in sig.parameters.items():
        if param.annotation != inspect.Parameter.empty:
            new_annotations[param_name] = _add_string_to_annotation(param.annotation)
        else:
            new_annotations[param_name] = param.annotation

    # Keep the return annotation unchanged
    if hasattr(func, "__annotations__") and "return" in func.__annotations__:
        new_annotations["return"] = func.__annotations__["return"]

    if iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Convert keyword arguments that should be dicts but are strings
            converted_kwargs = {}
            for param_name, param_value in kwargs.items():
                if param_name in sig.parameters:
                    param = sig.parameters[param_name]
                    if _should_convert_to_dict(param.annotation, param_value):
                        try:
                            converted_kwargs[param_name] = json.loads(param_value)
                            logger.debug(
                                f"Converted JSON string to dict for parameter '{param_name}': {param_value}"
                            )
                        except json.JSONDecodeError:
                            # If it's not valid JSON, pass the string as-is
                            converted_kwargs[param_name] = param_value
                    else:
                        converted_kwargs[param_name] = param_value
                else:
                    converted_kwargs[param_name] = param_value

            return await func(*args, **converted_kwargs)

        # Set the modified annotations on the wrapper
        async_wrapper.__annotations__ = new_annotations
        return async_wrapper

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        # Convert keyword arguments that should be dicts but are strings
        converted_kwargs = {}
        for param_name, param_value in kwargs.items():
            if param_name in sig.parameters:
                param = sig.parameters[param_name]
                if _should_convert_to_dict(param.annotation, param_value):
                    try:
                        converted_kwargs[param_name] = json.loads(param_value)
                        logger.debug(
                            f"Converted JSON string to dict for parameter '{param_name}': {param_value}"
                        )
                    except json.JSONDecodeError:
                        # If it's not valid JSON, pass the string as-is
                        converted_kwargs[param_name] = param_value
                else:
                    converted_kwargs[param_name] = param_value
            else:
                converted_kwargs[param_name] = param_value

        return func(*args, **converted_kwargs)

    # Set the modified annotations on the wrapper
    sync_wrapper.__annotations__ = new_annotations
    return sync_wrapper


def _update_schema_for_json_args(func: Callable, tool) -> None:
    """
    Modify the tool's JSON schema to accept strings for dict parameters.

    This function updates the input schema to allow JSON strings in addition to objects for
    parameters that are annotated as dict types, enabling MCP clients to pass JSON strings
    that will be automatically converted to dicts.

    This modification is always applied to ensure compatibility with various MCP clients.

    Args:
        func: The original function
        tool: The FastMCP tool object
    """
    try:
        sig = signature(func)

        # Get the MCP tool representation to modify its schema
        mcp_tool_dict = tool.to_mcp_tool().model_dump()
        input_schema = mcp_tool_dict.get("inputSchema", {})
        properties = input_schema.get("properties", {})

        # Check each parameter in the function signature
        for param_name, param in sig.parameters.items():
            if param_name in properties:
                param_schema = properties[param_name]

                # Check if this parameter should support JSON string conversion
                annotation = param.annotation
                should_support_string = _is_dict_compatible_annotation(annotation)

                if should_support_string:
                    # Modify the schema to also accept strings
                    if "anyOf" in param_schema:
                        # For Optional[dict] - add string to the anyOf list
                        existing_schemas = param_schema["anyOf"]
                        # Check if string is already in the schema
                        has_string = any(
                            s.get("type") == "string" for s in existing_schemas
                        )
                        if not has_string:
                            existing_schemas.append(
                                {
                                    "type": "string",
                                    "description": "JSON string that will be parsed to object",
                                }
                            )
                    elif param_schema.get("type") == "object":
                        # For dict - convert to anyOf with object and string
                        original_schema = param_schema.copy()
                        properties[param_name] = {
                            "anyOf": [
                                original_schema,
                                {
                                    "type": "string",
                                    "description": "JSON string that will be parsed to object",
                                },
                            ],
                            "title": param_schema.get("title", param_name.title()),
                        }
                        # Preserve default if it exists
                        if "default" in param_schema:
                            properties[param_name]["default"] = param_schema["default"]

        # Update the tool's parameters with the modified schema
        tool.parameters = input_schema

        logger.debug(
            f"Modified schema for tool '{tool.name}' to support JSON strings for dict parameters"
        )

    except Exception as e:
        logger.warning(f"Could not modify schema for JSON string support: {e}")


class MCPServer(LoggingConfigurable):
    """Simple MCP server that allows registering Python functions as tools."""

    # Configurable traits
    name = Unicode(
        default_value="Jupyter MCP Server", help="Name for the MCP server"
    ).tag(config=True)

    port = Int(default_value=3001, help="Port for the MCP server to listen on").tag(
        config=True
    )

    host = Unicode(
        default_value="localhost", help="Host for the MCP server to listen on"
    ).tag(config=True)

    def __init__(self, **kwargs):
        """Initialize the MCP server.

        Args:
            **kwargs: Configuration parameters
        """
        super().__init__(**kwargs)

        # Initialize FastMCP and tools/prompts registry
        self.mcp = FastMCP(self.name)
        self._registered_tools = {}
        self._registered_prompts = {}
        self.log.info(
            f"Initialized MCP server '{self.name}' on {self.host}:{self.port}"
        )

    def register_tool(
        self,
        func: Callable,
        name: str | None = None,
        description: str | None = None,
    ):
        """Register a Python function as an MCP tool.

        Args:
            func: Python function to register
            name: Optional tool name (defaults to function name)
            description: Optional tool description (defaults to function
                docstring)
        """
        tool_name = name or func.__name__
        tool_description = description or func.__doc__ or f"Tool: {tool_name}"

        self.log.info(f"Registering tool: {tool_name}")
        self.log.debug(
            f"Tool details - Name: {tool_name}, "
            f"Description: {tool_description}, Async: {iscoroutinefunction(func)}"
        )

        # Apply auto-conversion wrapper (always enabled)
        registered_func = _wrap_with_json_conversion(func)
        self.log.debug(f"Applied JSON argument auto-conversion wrapper to {tool_name}")

        # Register with FastMCP
        tool = self.mcp.tool(registered_func)

        # Modify schema to support JSON strings for dict parameters
        if tool:
            _update_schema_for_json_args(func, tool)
            self.log.debug(
                f"Modified schema for tool '{tool_name}' to accept JSON strings for dict parameters"
            )

        # Keep track for listing
        self._registered_tools[tool_name] = {
            "name": tool_name,
            "description": tool_description,
            "function": func,
            "is_async": iscoroutinefunction(func),
        }

    def register_tools(self, tools: list[Callable] | dict[str, Callable]):
        """Register multiple Python functions as MCP tools.

        Args:
            tools: List of functions or dict mapping names to functions
        """
        if isinstance(tools, list):
            for func in tools:
                self.register_tool(func)
        elif isinstance(tools, dict):
            for name, func in tools.items():
                self.register_tool(func, name=name)
        else:
            msg = "tools must be a list of functions or dict mapping names to functions"
            raise ValueError(msg)

    def list_tools(self) -> list[dict[str, Any]]:
        """List all registered tools."""
        return [
            {"name": tool["name"], "description": tool["description"]}
            for tool in self._registered_tools.values()
        ]

    def get_tool_info(self, tool_name: str) -> dict[str, Any] | None:
        """Get information about a specific tool."""
        return self._registered_tools.get(tool_name)

    def register_prompt(
        self,
        func: Callable,
        name: str | None = None,
        description: str | None = None,
    ):
        """Register a Python function as an MCP prompt.

        Args:
            func: Python function to register as a prompt
            name: Optional prompt name (defaults to function name)
            description: Optional prompt description (defaults to function
                docstring)
        """
        prompt_name = name or func.__name__
        prompt_description = description or func.__doc__ or f"Prompt: {prompt_name}"

        self.log.info(f"Registering prompt: {prompt_name}")
        self.log.debug(
            f"Prompt details - Name: {prompt_name}, "
            f"Description: {prompt_description}, Async: {iscoroutinefunction(func)}"
        )

        # Register with FastMCP using the @mcp.prompt decorator
        self.mcp.prompt(func)

        # Keep track for listing
        self._registered_prompts[prompt_name] = {
            "name": prompt_name,
            "description": prompt_description,
            "function": func,
            "is_async": iscoroutinefunction(func),
        }

    def register_prompts(self, prompts: list[Callable] | dict[str, Callable]):
        """Register multiple Python functions as MCP prompts.

        Args:
            prompts: List of functions or dict mapping names to functions
        """
        if isinstance(prompts, list):
            for func in prompts:
                self.register_prompt(func)
        elif isinstance(prompts, dict):
            for name, func in prompts.items():
                self.register_prompt(func, name=name)
        else:
            msg = "prompts must be a list of functions or dict mapping names to functions"
            raise ValueError(msg)

    def list_prompts(self) -> list[dict[str, Any]]:
        """List all registered prompts."""
        return [
            {"name": prompt["name"], "description": prompt["description"]}
            for prompt in self._registered_prompts.values()
        ]

    def get_prompt_info(self, prompt_name: str) -> dict[str, Any] | None:
        """Get information about a specific prompt."""
        return self._registered_prompts.get(prompt_name)

    async def start_server(self, host: str | None = None):
        """Start the MCP server on the specified host and port."""
        server_host = host or self.host

        self.log.info(f"Starting MCP server '{self.name}' on {server_host}:{self.port}")
        self.log.info(f"Registered tools: {list(self._registered_tools.keys())}")
        self.log.debug(f"Server configuration - Host: {server_host}, Port: {self.port}")

        # Start FastMCP server with HTTP transport
        await self.mcp.run_http_async(host=server_host, port=self.port)
