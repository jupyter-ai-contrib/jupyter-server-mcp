"""Hard-wired FastMCP middleware that routes JupyterLab frontend commands to the
specific web client (browser tab) that triggered the current tool call.

Background
----------
``jupyterlab-commands-toolkit`` lets a server-side tool run a JupyterLab command
on connected web clients by emitting a ``lab_command`` event. Historically that
event ran on *every* connected browser, so an AI editing a notebook in one tab
could corrupt a different notebook open in another tab. See
https://github.com/jupyterlab/jupyter-ai/issues/1650.

How routing works
-----------------
``jupyter-ai-persona-manager`` attaches two identity headers to the built-in
Jupyter MCP server it exposes to each persona:

- ``X-Jupyter-Chat-Id``: the chat the persona belongs to
- ``X-JupyterAI-Persona-Id``: the persona's id

These headers ride through to every ``tools/call`` request. On each tool call
this middleware:

1. reads the two headers,
2. looks up the persona in the server-app registry
   (``settings["jupyter-ai"]["persona-managers"][chat_id].personas[persona_id]``),
3. reads the ``web_client_id`` from the message the persona is currently
   processing (``persona.processing_message.metadata["web_client_id"]``), and
4. publishes it via the ``target_client_id`` ``ContextVar`` defined in
   ``jupyterlab-commands-toolkit``.

``jupyterlab_commands_toolkit.tools.execute_command`` reads that ``ContextVar``
and stamps ``client_id`` on the emitted event, so only the matching browser tab
runs the command.

Safe degradation
----------------
Every hop is defensive. If the toolkit is not installed, the headers are absent,
the persona can't be found, or it is not processing a message carrying a
``web_client_id``, the ``ContextVar`` is left unset and commands broadcast to all
clients (the pre-routing behavior). This keeps ``jupyter-server-mcp`` usable on
its own, with no hard dependency on either the toolkit or the persona manager.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware

logger = logging.getLogger(__name__)

#: Request header carrying the chat id (lower-cased; ``get_http_headers`` lowers keys).
CHAT_ID_HEADER = "x-jupyter-chat-id"
#: Request header carrying the persona id.
PERSONA_ID_HEADER = "x-jupyterai-persona-id"
#: Key under ``message.metadata`` holding the originating web client id.
WEB_CLIENT_ID_METADATA_KEY = "web_client_id"


class ClientRoutingMiddleware(Middleware):
    """Bind the target web client id for the duration of each tool call.

    Args:
        get_settings: Optional callable returning the Jupyter Server
            ``web_app.settings`` dict. Defaults to reading the running
            ``ServerApp`` singleton. Injectable for testing.
    """

    def __init__(self, get_settings: Callable[[], dict] | None = None) -> None:
        self._get_settings = get_settings

    def _settings(self) -> dict:
        if self._get_settings is not None:
            return self._get_settings()
        from jupyter_server.serverapp import ServerApp  # noqa: PLC0415

        return ServerApp.instance().web_app.settings

    def _resolve_web_client_id(self) -> str | None:
        """Resolve the web client id for the current call, or ``None`` to broadcast."""
        headers = get_http_headers()
        chat_id = headers.get(CHAT_ID_HEADER)
        persona_id = headers.get(PERSONA_ID_HEADER)
        if not chat_id or not persona_id:
            return None
        try:
            managers = (
                self._settings().get("jupyter-ai", {}).get("persona-managers", {})
            )
            manager = managers.get(chat_id)
            if manager is None:
                return None
            persona = manager.personas.get(persona_id)
            if persona is None:
                return None
            message = getattr(persona, "processing_message", None)
            if message is None:
                return None
            metadata = getattr(message, "metadata", None) or {}
            return metadata.get(WEB_CLIENT_ID_METADATA_KEY)
        except Exception:
            logger.debug("Client-routing lookup failed", exc_info=True)
            return None

    async def on_call_tool(self, context: Any, call_next: Callable) -> Any:
        # Optional dependency: without the toolkit there is nothing to set, so
        # the middleware is a transparent pass-through.
        try:
            from jupyterlab_commands_toolkit.tools import (  # noqa: PLC0415
                target_client_id,
            )
        except Exception:  # noqa: BLE001 - toolkit is an optional dependency
            return await call_next(context)

        token = target_client_id.set(self._resolve_web_client_id())
        try:
            return await call_next(context)
        finally:
            target_client_id.reset(token)
