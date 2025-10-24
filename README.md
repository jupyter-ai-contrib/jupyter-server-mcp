# Jupyter Server MCP Extension

A configurable MCP (Model Context Protocol) server extension for Jupyter Server that allows dynamic registration of Python functions as tools accessible to MCP clients from a running Jupyter Server.

https://github.com/user-attachments/assets/aa779b1c-a443-48d7-b3eb-13f27a4333b3

## Overview

This extension provides a simplified, trait-based approach to exposing Jupyter functionality through the MCP protocol. It can dynamically load and register tools from various Python packages, making them available to AI assistants and other MCP clients.

## Key Features

- **Simplified Architecture**: Direct function registration without complex abstractions
- **Configurable Tool Loading**: Register tools via string specifications (`module:function`)
- **Automatic Tool Discovery**: Python packages can expose tools via entrypoints
- **Jupyter Integration**: Seamless integration with Jupyter Server extension system
- **HTTP Transport**: FastMCP-based HTTP server with proper MCP protocol support
- **Traitlets Configuration**: Full configuration support through Jupyter's traitlets system

## Installation

```bash
pip install jupyter-server-mcp
```

## Quick Start

### 1. Basic Configuration

Create a `jupyter_config.py` file:

```python
c = get_config()

# Basic MCP server settings
c.MCPExtensionApp.mcp_name = "My Jupyter MCP Server"
c.MCPExtensionApp.mcp_port = 8080

# Register tools from existing packages
c.MCPExtensionApp.mcp_tools = [
    # Standard library tools
    "os:getcwd",
    "json:dumps",
    "time:time",
    
    # Jupyter AI Tools - Notebook operations  
    "jupyter_ai_tools.toolkits.notebook:read_notebook",
    "jupyter_ai_tools.toolkits.notebook:edit_cell",
    
    # JupyterLab Commands Toolkit
    "jupyterlab_commands_toolkit.tools:list_all_commands",
    "jupyterlab_commands_toolkit.tools:execute_command",
]
```

### 2. Start Jupyter Server

```bash
jupyter lab --config=jupyter_config.py
```

The MCP server will start automatically on `http://localhost:8080/mcp`.

### 3. Connect MCP Clients

**Claude Code Configuration:**

Set the following configuration:

```json
"mcpServers": {
  "jupyter-mcp": {
    "type": "http",
    "url": "http://localhost:8085/mcp"
  }
}
```

Or use the `claude` CLI:

```bash
claude mcp add --transport http jupyter-mcp http://localhost:8080/mcp
```

**Gemini CLI Configuration:**

Add the following to `.gemini/settings.json`:

```json
{
  "mcpServers": {
    "jupyter-mcp": {
      "httpUrl": "http://localhost:8080/mcp"
    }
  }
}
```

## Architecture

### Core Components

#### MCPServer (`jupyter_server_mcp.mcp_server.MCPServer`)

A simplified LoggingConfigurable class that manages FastMCP integration:

```python
from jupyter_server_mcp.mcp_server import MCPServer

# Create server
server = MCPServer(name="My Server", port=8080)

# Register functions
def my_tool(message: str) -> str:
    return f"Hello, {message}!"

server.register_tool(my_tool)

# Start server
await server.start_server()
```

**Key Methods:**
- `register_tool(func, name=None, description=None)` - Register a Python function
- `register_tools(tools)` - Register multiple functions (list or dict)
- `list_tools()` - Get list of registered tools
- `start_server(host=None)` - Start the HTTP MCP server

#### MCPExtensionApp (`jupyter_server_mcp.extension.MCPExtensionApp`)

Jupyter Server extension that manages the MCP server lifecycle:

**Configuration Traits:**
- `mcp_name` - Server name (default: "Jupyter MCP Server")
- `mcp_port` - Server port (default: 3001)
- `mcp_tools` - List of tools to register (format: "module:function")
- `use_tool_discovery` - Enable automatic tool discovery via entrypoints (default: True)

### Tool Registration

Tools can be registered in two ways:

#### 1. Manual Configuration

Specify tools directly in your Jupyter configuration using `module:function` format:

```python
c.MCPExtensionApp.mcp_tools = [
    "os:getcwd",
    "jupyter_ai_tools.toolkits.notebook:read_notebook",
]
```

#### 2. Automatic Discovery via Entrypoints

Python packages can expose tools automatically using the `jupyter_server_mcp.tools` entrypoint group.

**In your package's `pyproject.toml`:**

```toml
[project.entry-points."jupyter_server_mcp.tools"]
my_package_tools = "my_package.tools:TOOLS"
```

**In `my_package/tools.py`:**

```python
# Option 1: Define as a list
TOOLS = [
    "my_package.operations:create_file",
    "my_package.operations:delete_file",
]

# Option 2: Define as a function
def get_tools():
    return [
        "my_package.operations:create_file",
        "my_package.operations:delete_file",
    ]
```

Tools from entrypoints are discovered automatically when the extension starts. To disable automatic discovery:

```python
c.MCPExtensionApp.use_tool_discovery = False
```

## Configuration Examples

### Minimal Setup
```python
c = get_config()
c.MCPExtensionApp.mcp_port = 8080
```

### Full Configuration
```python
c = get_config()

# MCP Server Configuration
c.MCPExtensionApp.mcp_name = "Advanced Jupyter MCP Server"
c.MCPExtensionApp.mcp_port = 8080
c.MCPExtensionApp.mcp_tools = [
    # File system operations (jupyter-ai-tools)
    "jupyter_ai_tools.toolkits.file_system:read",
    "jupyter_ai_tools.toolkits.file_system:write", 
    "jupyter_ai_tools.toolkits.file_system:edit",
    "jupyter_ai_tools.toolkits.file_system:ls",
    "jupyter_ai_tools.toolkits.file_system:glob",
    
    # Notebook operations (jupyter-ai-tools)
    "jupyter_ai_tools.toolkits.notebook:read_notebook",
    "jupyter_ai_tools.toolkits.notebook:edit_cell",
    "jupyter_ai_tools.toolkits.notebook:add_cell", 
    "jupyter_ai_tools.toolkits.notebook:delete_cell",
    "jupyter_ai_tools.toolkits.notebook:create_notebook",
    
    # Git operations (jupyter-ai-tools)
    "jupyter_ai_tools.toolkits.git:git_status",
    "jupyter_ai_tools.toolkits.git:git_add",
    "jupyter_ai_tools.toolkits.git:git_commit",
    "jupyter_ai_tools.toolkits.git:git_push",
    
    # JupyterLab operations (jupyterlab-commands-toolkit)
    "jupyterlab_commands_toolkit.tools:clear_all_outputs_in_notebook",
    "jupyterlab_commands_toolkit.tools:open_document",
    "jupyterlab_commands_toolkit.tools:open_markdown_file_in_preview_mode",
    "jupyterlab_commands_toolkit.tools:show_diff_of_current_notebook",
    
    # Utility functions  
    "os:getcwd",
    "json:dumps",
    "time:time",
    "platform:system",
]
```

### Running Tests

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with coverage
pytest --cov=jupyter_server_mcp tests/
```

### Project Structure

```
jupyter_server_mcp/
├── jupyter_server_mcp/
│   ├── __init__.py
│   ├── mcp_server.py      # Core MCP server implementation
│   └── extension.py       # Jupyter Server extension
├── tests/
│   ├── test_mcp_server.py # MCPServer tests
│   └── test_extension.py  # Extension tests  
├── demo/
│   ├── jupyter_config.py  # Example configuration
│   └── *.py              # Debug/diagnostic scripts
└── pyproject.toml         # Package configuration
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality  
4. Ensure all tests pass: `pytest tests/`
5. Submit a pull request
