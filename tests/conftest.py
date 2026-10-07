"""Pytest configuration and fixtures for jupyter-server-docs-mcp tests."""

import asyncio
import tempfile
from pathlib import Path

import pytest
from tornado.web import Application

from jupyter_server_mcp.extension import MCPExtensionApp
from jupyter_server_mcp.mcp_server import MCPServer


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def socket_dir():
    """Create a private directory for Unix sockets.

    It lives under /tmp because ``tmp_path`` can exceed the socket path limit
    (104 bytes on macOS).
    """
    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mcp_extension():
    """Create an MCP extension instance for testing."""
    extension = MCPExtensionApp()
    extension.mcp_port = 3099  # Use high port to avoid conflicts
    return extension


@pytest.fixture
def mock_serverapp():
    """Create a mock Jupyter Server app for testing."""

    class MockServerApp:
        def __init__(self):
            self.log = MockLogger()
            self.web_app = Application()  # Add required web_app attribute

    class MockLogger:
        def info(self, msg):
            print(f"INFO: {msg}")

        def error(self, msg):
            print(f"ERROR: {msg}")

        def warning(self, msg):
            print(f"WARNING: {msg}")

    return MockServerApp()


@pytest.fixture
def mcp_server_simple():
    """Create a simple MCP server for testing."""
    return MCPServer(port=3097, log_level="WARNING")  # Use WARNING to reduce test noise


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "integration: mark test as integration test")


# Auto-use asyncio for async tests
pytest_plugins = ["pytest_asyncio"]
