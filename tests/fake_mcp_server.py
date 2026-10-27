"""A minimal hand-rolled MCP server used by the test suite.

Speaks newline-delimited JSON-RPC 2.0 over stdio — just enough of the
MCP protocol (initialize, tools/list, tools/call) to exercise Nova's
client without depending on the SDK. Doubles as a conformance check
that our client talks plain JSON-RPC correctly.

Environment knobs:
    FAKE_EXIT_AFTER="tools_list"   exit(9) right after answering tools/list
"""

import json
import os
import sys
import threading

PROTOCOL_VERSION = "2024-11-05"

# Mirrors github-mcp-server's stderr report that the browser login finished.
AUTH_COMPLETE_LINE = 'time=2026-08-23T00:00:00Z level=INFO msg="github authorization complete"'

TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the given text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "boom",
        "description": "Always fails.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "needs_login",
        "description": "Always fails with a GitHub-style authorization challenge.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "needs_login_ok",
        "description": "Returns a GitHub-style OAuth login request as a *successful* result.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def reply(msg_id: int, result: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> None:
    listed = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method", "")
        msg_id = msg.get("id")

        if method == "initialize":
            reply(
                msg_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "0.1.0"},
                },
            )
            if os.environ.get("FAKE_EXIT_AFTER") == "initialize":
                os._exit(9)
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            reply(msg_id, {"tools": TOOLS})
            listed = True
            if os.environ.get("FAKE_EXIT_AFTER") == "tools_list":
                os._exit(9)
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments", {})
            if name == "echo":
                reply(
                    msg_id,
                    {
                        "content": [{"type": "text", "text": f"echo: {args.get('text', '')}"}],
                        "isError": False,
                    },
                )
            elif name == "needs_login":
                reply(
                    msg_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "The GitHub MCP Server requires authorization. "
                                    "Visit https://github.com/login/device in your browser "
                                    "and enter the code WDJB-MJHT to authorize it. "
                                    "After authorization, retry your request."
                                ),
                            }
                        ],
                        "isError": True,
                    },
                )
                # Simulate the user completing the login: like the real
                # server, report completion on stderr a moment later.
                # Opt-in so unrelated tests aren't affected by the timing.
                if os.environ.get("FAKE_AUTH_REPORT") == "1":
                    threading.Timer(
                        0.4,
                        lambda: (sys.stderr.write(AUTH_COMPLETE_LINE + "\n"), sys.stderr.flush()),
                    ).start()
            elif name == "needs_login_ok":
                reply(
                    msg_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Authorization required. Open this URL in "
                                    "your browser to grant permissions:\n"
                                    "https://github.com/login/oauth/authorize"
                                    "?client_id=Ov23liABCDEF&code_challenge=abc"
                                    "&code_challenge_method=S256&redirect_uri="
                                    "http%3A%2F%2Flocalhost%3A8085%2Fcallback"
                                    "&response_type=code&scope=repo+read%3Aorg"
                                    "&state=xyz\n"
                                    "After authorization, retry your request."
                                ),
                            }
                        ],
                        "isError": False,
                    },
                )
            else:
                reply(
                    msg_id,
                    {"content": [{"type": "text", "text": "boom failed"}], "isError": True},
                )
        elif msg_id is not None:
            reply(msg_id, {})
        _ = listed


if __name__ == "__main__":
    main()
