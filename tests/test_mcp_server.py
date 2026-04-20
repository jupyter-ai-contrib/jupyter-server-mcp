"""Test the simplified MCP server functionality."""

import asyncio

import pytest

from jupyter_server_mcp.mcp_server import MCPServer, _wrap_with_json_conversion


def simple_function(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y


async def async_function(name: str) -> str:
    """Async greeting function."""
    await asyncio.sleep(0.001)  # Small delay
    return f"Hello, {name}!"


def function_with_docstring(message: str) -> str:
    """Print a message to stdout.

    This is a more detailed description of what this function does.
    """
    print(message)
    return f"Printed: {message}"


def simple_prompt(topic: str) -> str:
    """Generate a prompt about a topic."""
    return f"Please explain the concept of '{topic}' in detail."


async def async_prompt(task: str, language: str = "Python") -> str:
    """Generate a code generation prompt."""
    await asyncio.sleep(0.001)  # Small delay
    return f"Write a {language} function that performs the following task: {task}"


def prompt_with_detailed_docstring(file_path: str, focus: str = "general") -> str:
    """Generate a code review prompt.

    This prompt helps reviewers focus on specific aspects of code quality.
    Supports multiple focus areas including security, performance, and readability.
    """
    return f"Review the code in {file_path} with focus on {focus}."


class TestMCPServer:
    """Test MCPServer functionality."""

    def test_server_creation(self):
        """Test basic server creation."""
        server = MCPServer()

        assert server.name == "Jupyter MCP Server"
        assert server.port == 3001
        assert server.host == "localhost"
        assert server.mcp is not None
        assert len(server._registered_tools) == 0
        assert len(server._registered_prompts) == 0

    def test_server_creation_with_params(self):
        """Test server creation with custom parameters."""
        server = MCPServer(name="Test Server", port=3050, host="0.0.0.0")

        assert server.name == "Test Server"
        assert server.port == 3050
        assert server.host == "0.0.0.0"
        assert server.mcp is not None

    def test_register_single_tool(self):
        """Test registering a single tool."""
        server = MCPServer()

        server.register_tool(simple_function)

        # Check tool was registered
        assert len(server._registered_tools) == 1
        assert "simple_function" in server._registered_tools

        tool_info = server._registered_tools["simple_function"]
        assert tool_info["name"] == "simple_function"
        assert tool_info["description"] == "Add two numbers."
        assert tool_info["function"] == simple_function
        assert tool_info["is_async"] is False

    def test_register_tool_with_custom_name(self):
        """Test registering a tool with custom name."""
        server = MCPServer()

        server.register_tool(simple_function, name="add_numbers")

        assert "add_numbers" in server._registered_tools
        assert "simple_function" not in server._registered_tools

        tool_info = server._registered_tools["add_numbers"]
        assert tool_info["name"] == "add_numbers"

    def test_register_tool_with_custom_description(self):
        """Test registering a tool with custom description."""
        server = MCPServer()

        server.register_tool(simple_function, description="Custom description")

        tool_info = server._registered_tools["simple_function"]
        assert tool_info["description"] == "Custom description"

    def test_register_async_tool(self):
        """Test registering an async tool."""
        server = MCPServer()

        server.register_tool(async_function)

        tool_info = server._registered_tools["async_function"]
        assert tool_info["is_async"] is True

    def test_register_tools_as_list(self):
        """Test registering multiple tools as a list."""
        server = MCPServer()

        server.register_tools([simple_function, async_function])

        assert len(server._registered_tools) == 2
        assert "simple_function" in server._registered_tools
        assert "async_function" in server._registered_tools

    def test_register_tools_as_dict(self):
        """Test registering multiple tools as a dict."""
        server = MCPServer()

        tools = {"add": simple_function, "greet": async_function}
        server.register_tools(tools)

        assert len(server._registered_tools) == 2
        assert "add" in server._registered_tools
        assert "greet" in server._registered_tools

        assert server._registered_tools["add"]["function"] == simple_function
        assert server._registered_tools["greet"]["function"] == async_function

    def test_register_tools_invalid_type(self):
        """Test registering tools with invalid type."""
        server = MCPServer()

        with pytest.raises(
            ValueError, match="tools must be a list of functions or dict"
        ):
            server.register_tools("invalid")

    def test_list_tools(self):
        """Test listing registered tools."""
        server = MCPServer()

        # Initially empty
        assert server.list_tools() == []

        # After registering tools
        server.register_tool(simple_function)
        server.register_tool(function_with_docstring)

        tools = server.list_tools()
        assert len(tools) == 2

        tool_names = [t["name"] for t in tools]
        assert "simple_function" in tool_names
        assert "function_with_docstring" in tool_names

        # Check structure
        for tool in tools:
            assert "name" in tool
            assert "description" in tool

    def test_get_tool_info(self):
        """Test getting tool information."""
        server = MCPServer()

        # Non-existent tool
        assert server.get_tool_info("nonexistent") is None

        # Existing tool
        server.register_tool(simple_function)
        info = server.get_tool_info("simple_function")

        assert info is not None
        assert info["name"] == "simple_function"
        assert info["function"] == simple_function

    def test_register_single_prompt(self):
        """Test registering a single prompt."""
        server = MCPServer()

        server.register_prompt(simple_prompt)

        # Check prompt was registered
        assert len(server._registered_prompts) == 1
        assert "simple_prompt" in server._registered_prompts

        prompt_info = server._registered_prompts["simple_prompt"]
        assert prompt_info["name"] == "simple_prompt"
        assert prompt_info["description"] == "Generate a prompt about a topic."
        assert prompt_info["function"] == simple_prompt
        assert prompt_info["is_async"] is False

    def test_register_prompt_with_custom_name(self):
        """Test registering a prompt with custom name."""
        server = MCPServer()

        server.register_prompt(simple_prompt, name="topic_explainer")

        assert "topic_explainer" in server._registered_prompts
        assert "simple_prompt" not in server._registered_prompts

        prompt_info = server._registered_prompts["topic_explainer"]
        assert prompt_info["name"] == "topic_explainer"

    def test_register_prompt_with_custom_description(self):
        """Test registering a prompt with custom description."""
        server = MCPServer()

        server.register_prompt(simple_prompt, description="Custom prompt description")

        prompt_info = server._registered_prompts["simple_prompt"]
        assert prompt_info["description"] == "Custom prompt description"

    def test_register_async_prompt(self):
        """Test registering an async prompt."""
        server = MCPServer()

        server.register_prompt(async_prompt)

        prompt_info = server._registered_prompts["async_prompt"]
        assert prompt_info["is_async"] is True

    def test_register_prompts_as_list(self):
        """Test registering multiple prompts as a list."""
        server = MCPServer()

        server.register_prompts([simple_prompt, async_prompt])

        assert len(server._registered_prompts) == 2
        assert "simple_prompt" in server._registered_prompts
        assert "async_prompt" in server._registered_prompts

    def test_register_prompts_as_dict(self):
        """Test registering multiple prompts as a dict."""
        server = MCPServer()

        prompts = {"explainer": simple_prompt, "code_gen": async_prompt}
        server.register_prompts(prompts)

        assert len(server._registered_prompts) == 2
        assert "explainer" in server._registered_prompts
        assert "code_gen" in server._registered_prompts

        assert server._registered_prompts["explainer"]["function"] == simple_prompt
        assert server._registered_prompts["code_gen"]["function"] == async_prompt

    def test_register_prompts_invalid_type(self):
        """Test registering prompts with invalid type."""
        server = MCPServer()

        with pytest.raises(
            ValueError, match="prompts must be a list of functions or dict"
        ):
            server.register_prompts("invalid")

    def test_list_prompts(self):
        """Test listing registered prompts."""
        server = MCPServer()

        # Initially empty
        assert server.list_prompts() == []

        # After registering prompts
        server.register_prompt(simple_prompt)
        server.register_prompt(prompt_with_detailed_docstring)

        prompts = server.list_prompts()
        assert len(prompts) == 2

        prompt_names = [p["name"] for p in prompts]
        assert "simple_prompt" in prompt_names
        assert "prompt_with_detailed_docstring" in prompt_names

        # Check structure
        for prompt in prompts:
            assert "name" in prompt
            assert "description" in prompt

    def test_get_prompt_info(self):
        """Test getting prompt information."""
        server = MCPServer()

        # Non-existent prompt
        assert server.get_prompt_info("nonexistent") is None

        # Existing prompt
        server.register_prompt(simple_prompt)
        info = server.get_prompt_info("simple_prompt")

        assert info is not None
        assert info["name"] == "simple_prompt"
        assert info["function"] == simple_prompt

    def test_tools_and_prompts_together(self):
        """Test registering both tools and prompts in the same server."""
        server = MCPServer()

        server.register_tool(simple_function)
        server.register_prompt(simple_prompt)

        assert len(server._registered_tools) == 1
        assert len(server._registered_prompts) == 1
        assert "simple_function" in server._registered_tools
        assert "simple_prompt" in server._registered_prompts


class TestMCPServerDirect:
    """Test direct MCPServer instantiation."""

    def test_create_server_defaults(self):
        """Test creating server with defaults."""
        server = MCPServer()

        assert isinstance(server, MCPServer)
        assert server.name == "Jupyter MCP Server"
        assert server.port == 3001

    def test_create_server_with_params(self):
        """Test creating server with custom parameters."""
        server = MCPServer(name="Custom Server", port=3055)

        assert server.name == "Custom Server"
        assert server.port == 3055


class TestMCPServerIntegration:
    """Integration tests for MCP server."""

    @pytest.mark.integration
    def test_server_with_multiple_tools(self):
        """Test server with multiple different types of tools."""
        server = MCPServer(name="Integration Test Server")

        # Register various types of tools
        server.register_tool(simple_function)
        server.register_tool(async_function)
        server.register_tool(function_with_docstring, name="printer")

        # Verify all registered
        assert len(server._registered_tools) == 3
        tools = server.list_tools()
        tool_names = [t["name"] for t in tools]

        assert "simple_function" in tool_names
        assert "async_function" in tool_names
        assert "printer" in tool_names

        # Verify async detection
        assert server._registered_tools["simple_function"]["is_async"] is False
        assert server._registered_tools["async_function"]["is_async"] is True
        assert server._registered_tools["printer"]["is_async"] is False


class TestJSONArgumentConversion:
    """Test JSON argument conversion functionality."""

    def test_simple_dict_conversion(self):
        """Test basic JSON string to dict conversion."""

        def func_with_dict(data: dict) -> dict:
            """Function that expects a dict."""
            return {"received": data, "type": type(data).__name__}

        wrapped_func = _wrap_with_json_conversion(func_with_dict)

        # Test with actual dict (should pass through)
        result = wrapped_func(data={"key": "value"})
        assert result["received"] == {"key": "value"}
        assert result["type"] == "dict"

        # Test with JSON string (should be converted)
        result = wrapped_func(data='{"key": "value"}')
        assert result["received"] == {"key": "value"}
        assert result["type"] == "dict"

    def test_optional_dict_conversion(self):
        """Test JSON conversion with Optional[dict] annotation."""

        def func_with_optional_dict(data: dict | None = None) -> dict:
            """Function with optional dict parameter."""
            return {
                "received": data,
                "type": type(data).__name__ if data else "NoneType",
            }

        wrapped_func = _wrap_with_json_conversion(func_with_optional_dict)

        # Test with None (should pass through)
        result = wrapped_func(data=None)
        assert result["received"] is None
        assert result["type"] == "NoneType"

        # Test with JSON string (should be converted)
        result = wrapped_func(data='{"optional": true}')
        assert result["received"] == {"optional": True}
        assert result["type"] == "dict"

    def test_union_dict_conversion(self):
        """Test JSON conversion with Union type annotations."""

        def func_with_union_dict(data: dict | None) -> dict:
            """Function with Union[dict, None] parameter."""
            return {
                "received": data,
                "type": type(data).__name__ if data else "NoneType",
            }

        wrapped_func = _wrap_with_json_conversion(func_with_union_dict)

        # Test with JSON string (should be converted)
        result = wrapped_func(data='{"union": "test"}')
        assert result["received"] == {"union": "test"}
        assert result["type"] == "dict"

    def test_typed_dict_conversion(self):
        """Test JSON conversion with typed dict annotations."""

        def func_with_typed_dict(config: dict[str, str]) -> dict:
            """Function with Dict[str, str] annotation."""
            return {"received": config, "type": type(config).__name__}

        wrapped_func = _wrap_with_json_conversion(func_with_typed_dict)

        # Test with JSON string (should be converted)
        result = wrapped_func(config='{"name": "test", "value": "data"}')
        assert result["received"] == {"name": "test", "value": "data"}
        assert result["type"] == "dict"

    def test_invalid_json_handling(self):
        """Test handling of invalid JSON strings."""

        def func_with_dict(data: dict) -> dict:
            """Function that expects a dict."""
            return {"received": data, "type": type(data).__name__}

        wrapped_func = _wrap_with_json_conversion(func_with_dict)

        # Test with invalid JSON (should pass string as-is)
        result = wrapped_func(data="invalid json {")
        assert result["received"] == "invalid json {"
        assert result["type"] == "str"

        # Test with empty string (should pass as-is)
        result = wrapped_func(data="")
        assert result["received"] == ""
        assert result["type"] == "str"

    def test_non_dict_parameters_unchanged(self):
        """Test that non-dict parameters are not affected."""

        def mixed_func(name: str, count: int, data: dict) -> dict:
            """Function with mixed parameter types."""
            return {
                "name": name,
                "name_type": type(name).__name__,
                "count": count,
                "count_type": type(count).__name__,
                "data": data,
                "data_type": type(data).__name__,
            }

        wrapped_func = _wrap_with_json_conversion(mixed_func)

        # Only the dict parameter should be converted
        result = wrapped_func(name="test", count=42, data='{"converted": true}')

        assert result["name"] == "test"
        assert result["name_type"] == "str"
        assert result["count"] == 42
        assert result["count_type"] == "int"
        assert result["data"] == {"converted": True}
        assert result["data_type"] == "dict"

    @pytest.mark.asyncio
    async def test_async_function_conversion(self):
        """Test JSON conversion with async functions."""

        async def async_func_with_dict(config: dict) -> dict:
            """Async function that expects a dict."""
            await asyncio.sleep(0.001)  # Small delay
            return {"async_result": config, "type": type(config).__name__}

        wrapped_func = _wrap_with_json_conversion(async_func_with_dict)

        # Test with JSON string (should be converted)
        result = await wrapped_func(config='{"async": true, "value": 123}')
        assert result["async_result"] == {"async": True, "value": 123}
        assert result["type"] == "dict"

    def test_complex_nested_json(self):
        """Test conversion of complex nested JSON structures."""

        def func_with_nested_dict(data: dict) -> dict:
            """Function that processes nested dict data."""
            return {"processed": data}

        wrapped_func = _wrap_with_json_conversion(func_with_nested_dict)

        complex_json = """{
            "users": [
                {"name": "Alice", "age": 30},
                {"name": "Bob", "age": 25}
            ],
            "metadata": {
                "version": "1.0",
                "created": "2024-01-01"
            }
        }"""

        result = wrapped_func(data=complex_json)
        expected = {
            "users": [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}],
            "metadata": {"version": "1.0", "created": "2024-01-01"},
        }
        assert result["processed"] == expected

    def test_annotation_modification(self):
        """Test that function annotations are properly modified."""

        def original_func(data: dict) -> dict:
            """Original function with dict annotation."""
            return data

        wrapped_func = _wrap_with_json_conversion(original_func)

        # Check that annotations were modified to accept strings
        annotations = wrapped_func.__annotations__
        assert "data" in annotations

        # The annotation should now be dict | str (or Union equivalent)
        data_annotation = annotations["data"]
        # We can check this works by ensuring both dict and str are acceptable
        assert hasattr(data_annotation, "__args__") or data_annotation == (dict | str)


class TestJSONSchemaModification:
    """Test JSON schema modification for MCP tools."""

    def test_schema_modification_applied(self):
        """Test that schema modification is applied during tool registration."""
        server = MCPServer()

        def func_with_dict_param(config: dict) -> str:
            """Function with dict parameter."""
            return f"Received config: {config}"

        # Register the function - schema should be automatically modified
        server.register_tool(func_with_dict_param)

        # Verify the tool was registered
        assert "func_with_dict_param" in server._registered_tools
        tool_info = server._registered_tools["func_with_dict_param"]
        assert tool_info["name"] == "func_with_dict_param"

    def test_multiple_dict_parameters(self):
        """Test conversion with multiple dict parameters."""

        def func_multiple_dicts(config: dict, metadata: dict, name: str) -> dict:
            """Function with multiple dict parameters."""
            return {
                "config": config,
                "metadata": metadata,
                "name": name,
                "types": {
                    "config": type(config).__name__,
                    "metadata": type(metadata).__name__,
                    "name": type(name).__name__,
                },
            }

        wrapped_func = _wrap_with_json_conversion(func_multiple_dicts)

        result = wrapped_func(
            config='{"key1": "value1"}', metadata='{"version": 2}', name="test_function"
        )

        assert result["config"] == {"key1": "value1"}
        assert result["metadata"] == {"version": 2}
        assert result["name"] == "test_function"
        assert result["types"]["config"] == "dict"
        assert result["types"]["metadata"] == "dict"
        assert result["types"]["name"] == "str"
