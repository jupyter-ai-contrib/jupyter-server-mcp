"""E2E tests for the hard-wired client-routing middleware.

These drive a real FastMCP server over streamable HTTP with a real MCP client,
sending the identity headers the persona manager attaches, and assert the
middleware resolves the originating web client id into the
``jupyterlab-commands-toolkit`` ``target_client_id`` ContextVar that a tool then
reads. This mirrors, end to end, how ``jupyter-ai-persona-manager`` +
``jupyterlab-commands-toolkit`` route a command to a single browser tab.
"""

import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from fastmcp import FastMCP
from jupyterlab_commands_toolkit.tools import target_client_id
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from jupyter_server_mcp.client_routing import (
    CHAT_ID_HEADER,
    PERSONA_ID_HEADER,
    ClientRoutingMiddleware,
)
from jupyter_server_mcp.mcp_server import MCPServer

# Mutable stand-in for the Jupyter Server ``web_app.settings`` the middleware
# reads. Tests mutate the persona registry before each call.
_FAKE_SETTINGS: dict = {"jupyter-ai": {"persona-managers": {}}}


def _set_registry(managers: dict) -> None:
    _FAKE_SETTINGS["jupyter-ai"]["persona-managers"] = managers


def _persona(web_client_id=None, *, processing=True):
    """Build a duck-typed persona with an in-flight message (or none)."""
    message = None
    if processing:
        metadata = {} if web_client_id is None else {"web_client_id": web_client_id}
        message = SimpleNamespace(metadata=metadata)
    return SimpleNamespace(processing_message=message)


def _manager(personas: dict):
    return SimpleNamespace(personas=personas)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # run cleanly in a thread
        pass


@pytest.fixture(scope="module")
def mcp_url():
    """A real FastMCP HTTP server with the routing middleware + a probe tool."""
    mcp = FastMCP("routing-test")
    mcp.add_middleware(ClientRoutingMiddleware(get_settings=lambda: _FAKE_SETTINGS))

    @mcp.tool
    async def whoami(delay: float = 0.0) -> dict:
        """Return the target web client id bound for this call (after an optional
        delay, so concurrent calls can be forced to overlap)."""
        if delay:
            await asyncio.sleep(delay)
        return {"wid": target_client_id.get()}

    port = _free_port()
    app = mcp.http_app(transport="http")
    server = _Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, lifespan="on", log_level="error"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    time.sleep(0.3)


async def _call_whoami(url: str, headers: dict, delay: float = 0.0):
    async with (
        streamablehttp_client(url, headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("whoami", {"delay": delay})
        return (result.structuredContent or {}).get("wid")


@pytest.mark.asyncio
async def test_routes_to_processing_web_client(mcp_url):
    """Headers + a persona processing a message → that message's web client id."""
    _set_registry({"chat-1": _manager({"persona-1": _persona("WID-A")})})
    wid = await _call_whoami(
        mcp_url, {CHAT_ID_HEADER: "chat-1", PERSONA_ID_HEADER: "persona-1"}
    )
    assert wid == "WID-A"


@pytest.mark.asyncio
async def test_no_headers_broadcasts(mcp_url):
    """No identity headers → ContextVar unset (None) → command broadcasts."""
    _set_registry({"chat-1": _manager({"persona-1": _persona("WID-A")})})
    wid = await _call_whoami(mcp_url, {})
    assert wid is None


@pytest.mark.asyncio
async def test_unknown_persona_broadcasts(mcp_url):
    """Headers naming an absent persona → None (broadcast), never an error."""
    _set_registry({"chat-1": _manager({})})
    wid = await _call_whoami(
        mcp_url, {CHAT_ID_HEADER: "chat-1", PERSONA_ID_HEADER: "nope"}
    )
    assert wid is None


@pytest.mark.asyncio
async def test_persona_not_processing_broadcasts(mcp_url):
    """Persona present but processing nothing → None (broadcast)."""
    _set_registry({"chat-1": _manager({"persona-1": _persona(processing=False)})})
    wid = await _call_whoami(
        mcp_url, {CHAT_ID_HEADER: "chat-1", PERSONA_ID_HEADER: "persona-1"}
    )
    assert wid is None


@pytest.mark.asyncio
async def test_concurrent_calls_are_isolated(mcp_url):
    """Two overlapping calls for different personas each resolve their own id.

    The tool sleeps while the ContextVar is set, guaranteeing the two calls are
    in flight simultaneously; a leak between them would surface as a swapped or
    dropped id.
    """
    _set_registry(
        {
            "chat-1": _manager({"persona-1": _persona("WID-A")}),
            "chat-2": _manager({"persona-2": _persona("WID-B")}),
        }
    )
    wid_a, wid_b = await asyncio.gather(
        _call_whoami(
            mcp_url,
            {CHAT_ID_HEADER: "chat-1", PERSONA_ID_HEADER: "persona-1"},
            delay=1.0,
        ),
        _call_whoami(
            mcp_url,
            {CHAT_ID_HEADER: "chat-2", PERSONA_ID_HEADER: "persona-2"},
            delay=1.0,
        ),
    )
    assert wid_a == "WID-A"
    assert wid_b == "WID-B"


def test_mcpserver_wires_routing_middleware():
    """MCPServer installs the routing middleware on its FastMCP instance."""
    server = MCPServer(name="wiring-test", port=0)
    middleware = getattr(server.mcp, "middleware", []) or []
    assert any(isinstance(m, ClientRoutingMiddleware) for m in middleware)
