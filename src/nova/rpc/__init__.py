"""JSON-RPC sidecar for the Electron frontend (``python -m nova.rpc``).

``NovaRpc`` is assembled from one mixin per area (chat, conversations,
models, settings, skills, MCP, terminal, system); ``server`` holds the stdio
transport and request dispatch.
"""

from nova.rpc.core import DEFAULT_SETTINGS, USER_SKILLS_DIR, NovaRpc
from nova.rpc.server import Server, Transport, build_server, main

__all__ = [
    "DEFAULT_SETTINGS",
    "USER_SKILLS_DIR",
    "NovaRpc",
    "Server",
    "Transport",
    "build_server",
    "main",
]
