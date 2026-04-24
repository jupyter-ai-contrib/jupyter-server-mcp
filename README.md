# Jupyter Server MCP Extension

[![PyPI version](https://img.shields.io/pypi/v/jupyter-server-mcp.svg)](https://pypi.org/project/jupyter-server-mcp/)
[![conda-forge version](https://img.shields.io/conda/vn/conda-forge/jupyter-server-mcp.svg)](https://anaconda.org/conda-forge/jupyter-server-mcp)

A configurable MCP (Model Context Protocol) server extension for Jupyter Server that allows dynamic registration of Python functions as tools accessible to MCP clients from a running Jupyter Server.

https://github.com/user-attachments/assets/aa779b1c-a443-48d7-b3eb-13f27a4333b3

## Overview

This extension provides a simplified, trait-based approach to exposing Jupyter functionality through the MCP protocol. It can dynamically load and register tools from various Python packages, making them available to AI assistants and other MCP clients.

## Key Features

- **Simplified Architecture**: Direct function registration without complex abstractions
- **Configurable Tool Loading**: Register tools via string specifications (`module:function`)
- **Automatic Tool Discovery**: Python packages can expose tools via entrypoints
- **Jupyter Integration**: Seamless integration with Jupyter Server extension system
- **Streamable HTTP Transport**: FastMCP-based HTTP server with proper MCP protocol support
- **Stdio Proxy**: Stable `jupyter-server-mcp-proxy` / `python -m jupyter_server_mcp.proxy` entry point that auto-discovers the running Jupyter MCP server — no hard-coded port needed in client configuration, and launchable via `uvx` from outside the Jupyter environment
- **Ephemeral Port by Default**: The MCP server binds a free port chosen by the OS unless you pin `mcp_port`, so multiple Jupyter servers can run in parallel without conflicts
- **Traitlets Configuration**: Full configuration support through Jupyter's traitlets system

## Installation

Install `jupyter-server-mcp` into the same environment as Jupyter Server or JupyterLab.

### pip

```bash
python -m pip install jupyter-server-mcp
```

### conda / mamba / micromamba

```bash
conda install -c conda-forge jupyter-server-mcp
mamba install -c conda-forge jupyter-server-mcp
micromamba install -c conda-forge jupyter-server-mcp
```

### pixi

In an existing Pixi workspace:

```bash
# If conda-forge is not already configured for the workspace
pixi project channel add conda-forge
pixi add jupyter-server-mcp
```

## Quick Start

### 1. Basic Configuration

Create a `jupyter_config.py` file:

```python
c = get_config()

# Basic MCP server settings
c.MCPExtensionApp.mcp_name = "My Jupyter MCP Server"

# Optional: pin the MCP server to a specific port.
# By default (mcp_port = 0), the OS picks a free ephemeral port, which lets
# multiple Jupyter servers coexist. Only set a fixed port when an MCP client
# connects directly over HTTP and needs a stable URL.
# c.MCPExtensionApp.mcp_port = 3001

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

By default, the MCP server binds an **ephemeral port** (the OS picks a free one),
so multiple Jupyter servers can run side-by-side without port conflicts. The stdio
proxy (see below) auto-discovers whichever port was chosen, so client configuration
does not need to change when the port does.

If you want a fixed, predictable URL — for example when connecting an MCP client
directly over HTTP — set `c.MCPExtensionApp.mcp_port = 3001` (or any other port)
in your config. Any trait can also be set directly on the command line:

```bash
jupyter lab --MCPExtensionApp.mcp_port=3001
```

When a fixed port is configured and already in use, startup fails with a clear
error instead of silently choosing another port.

### 3. CLI MCP Client Configuration

There are two supported ways to wire an MCP client to this extension:

1. **Stdio proxy (recommended)** — the client launches a small stdio proxy (`jupyter-server-mcp-proxy` or `python -m jupyter_server_mcp.proxy`), which auto-discovers the running Jupyter MCP server and bridges stdio to its HTTP endpoint. Because the server defaults to an ephemeral port, this is the only configuration that works unchanged when multiple Jupyter servers run side-by-side or when the port shifts between runs.
2. **Direct HTTP** — point the client at `http://localhost:<port>/mcp`. This requires pinning `c.MCPExtensionApp.mcp_port` to a known value, and works well when you run a single Jupyter server on a stable port.

When multiple Jupyter servers are running on the same machine, the stdio proxy picks the one whose Jupyter root directory is the most specific ancestor of the MCP client's current working directory. If no server's root directory contains that working directory, or if several tie, the proxy refuses to guess and asks you to disambiguate with `--url` or by setting `JUPYTER_SERVER_MCP_URL`.

The list below is intentionally curated rather than exhaustive and focuses on terminal-based coding agents.
For a broader, community-maintained directory of MCP-compatible clients, see the MCP client directory: <https://modelcontextprotocol.io/clients>.

#### Option A — Stdio proxy (recommended)

The proxy can be launched in two ways:

- **`uvx`** — ideal for MCP clients that are not installed inside the same
  Python environment as Jupyter. [uv](https://docs.astral.sh/uv/) installs
  `jupyter-server-mcp` into a cached, ephemeral environment and runs its
  `jupyter-server-mcp-proxy` console script. The runtime info file the
  extension writes is stored in the per-user Jupyter runtime directory, so
  auto-discovery works across environments.
- **`python -m jupyter_server_mcp.proxy`** — use when the client is
  already running in an environment that has `jupyter-server-mcp`
  installed.

The examples below show the `uvx` form. Swap in the `python -m` form by
replacing `"command": "uvx", "args": ["--from", "jupyter-server-mcp", "jupyter-server-mcp-proxy"]`
with `"command": "python", "args": ["-m", "jupyter_server_mcp.proxy"]`.

**Claude Code**

Add the following to `.mcp.json`:

```json
{
  "mcpServers": {
    "jupyter-mcp": {
      "command": "uvx",
      "args": ["--from", "jupyter-server-mcp", "jupyter-server-mcp-proxy"]
    }
  }
}
```

Or use the `claude` CLI:

```bash
claude mcp add jupyter-mcp -- uvx --from jupyter-server-mcp jupyter-server-mcp-proxy
```

**Codex**

Use the `codex` CLI:

```bash
codex mcp add jupyter-mcp -- uvx --from jupyter-server-mcp jupyter-server-mcp-proxy
```

Or add the following to `~/.codex/config.toml`:

```toml
[mcp_servers.jupyter-mcp]
command = "uvx"
args = ["--from", "jupyter-server-mcp", "jupyter-server-mcp-proxy"]
```

**OpenCode**

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "jupyter-mcp": {
      "type": "local",
      "command": ["uvx", "--from", "jupyter-server-mcp", "jupyter-server-mcp-proxy"],
      "enabled": true
    }
  }
}
```

**Gemini CLI**

```json
{
  "mcpServers": {
    "jupyter-mcp": {
      "command": "uvx",
      "args": ["--from", "jupyter-server-mcp", "jupyter-server-mcp-proxy"]
    }
  }
}
```

**Copilot CLI**

```json
{
  "mcpServers": {
    "jupyter-mcp": {
      "type": "local",
      "command": "uvx",
      "args": ["--from", "jupyter-server-mcp", "jupyter-server-mcp-proxy"],
      "tools": ["*"]
    }
  }
}
```

Pin to a specific version with `--from jupyter-server-mcp==X.Y.Z` when you
need to match the server side exactly.

The proxy accepts a few optional arguments (append them to `args`):

- `--url URL` — bypass auto-discovery and connect to an explicit MCP endpoint
- `--runtime-dir DIR` — look in a specific Jupyter runtime directory
- `--cwd DIR` — use a different directory when disambiguating between servers

`JUPYTER_SERVER_MCP_URL` is equivalent to `--url` and takes precedence over discovery when set.

#### Option B — Direct HTTP

Direct HTTP requires a fixed, known port. Configure one explicitly (the
default `mcp_port = 0` picks an ephemeral port each run, which is good for
the stdio proxy but not usable as a stable URL):

```python
c.MCPExtensionApp.mcp_port = 3001
```

The extension then exposes a FastMCP streamable HTTP endpoint at
`http://localhost:3001/mcp`. Replace `3001` below with whichever port you
chose. If a client asks for a transport type, pick `HTTP` or
`Streamable HTTP`.

**OpenCode**

Use `opencode mcp add`, or add the following to `opencode.json` or `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "jupyter-mcp": {
      "type": "remote",
      "url": "http://localhost:3001/mcp",
      "enabled": true
    }
  }
}
```

**Mistral Vibe**

Add the following to `./.vibe/config.toml` or `~/.vibe/config.toml`:

```toml
[[mcp_servers]]
name = "jupyter-mcp"
transport = "streamable-http"
url = "http://localhost:3001/mcp"
```

**Claude Code**

Add the following to `.mcp.json`:

```json
{
  "mcpServers": {
    "jupyter-mcp": {
      "type": "http",
      "url": "http://localhost:3001/mcp"
    }
  }
}
```

Or use the `claude` CLI:

```bash
claude mcp add --transport http jupyter-mcp http://localhost:3001/mcp
```

**Codex**

Use the `codex` CLI:

```bash
codex mcp add jupyter-mcp --url http://localhost:3001/mcp
```

Or add the following to `~/.codex/config.toml`:

```toml
[mcp_servers.jupyter-mcp]
url = "http://localhost:3001/mcp"
```

**Gemini CLI**

Add the following to `.gemini/settings.json`:

```json
{
  "mcpServers": {
    "jupyter-mcp": {
      "httpUrl": "http://localhost:3001/mcp"
    }
  }
}
```

**Copilot CLI**

Use `/mcp add` in interactive mode, or add the following to `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "jupyter-mcp": {
      "type": "http",
      "url": "http://localhost:3001/mcp",
      "tools": ["*"]
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
- `mcp_port` - Server port (default: 0, meaning the OS picks a free ephemeral port)
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

# Optional: pin the MCP server to a specific port (default: 0 — ephemeral).
# Only needed when an MCP client connects directly over HTTP.
# c.MCPExtensionApp.mcp_port = 3001
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
│   ├── extension.py       # Jupyter Server extension
│   ├── proxy.py           # Stdio MCP proxy entrypoint (python -m jupyter_server_mcp.proxy)
│   └── runtime.py         # Runtime info-file helpers shared between the extension and the proxy
├── tests/
│   ├── test_mcp_server.py # MCPServer tests
│   ├── test_extension.py  # Extension tests
│   ├── test_proxy.py      # Stdio proxy tests
│   └── test_runtime.py    # Runtime info-file helper tests
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
