# Connecting MCP servers

Nova can use any stdio-based [MCP](https://modelcontextprotocol.io) server. Click **MCP** in the header → **+ Add GitHub server** (or **Advanced → + Add custom server**). Nova writes the config, spawns the process and lists its tools; every tool call goes through the standard permission flow.

Manual config lives at `~/.config/nova/mcp.json`. The GitHub server needs the `stdio` subcommand plus the callback port (OAuth mode):

```json
{
  "mcpServers": {
    "github": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
               "-p", "127.0.0.1:8085:8085",
               "-e", "GITHUB_OAUTH_CALLBACK_PORT",
               "ghcr.io/github/github-mcp-server",
               "stdio", "--oauth-callback-port", "8085"],
      "env": { "GITHUB_OAUTH_CALLBACK_PORT": "8085" },
      "enabled": true
    }
  }
}
```

Auth modes (chosen in the **Add GitHub server** form):

- **OAuth (default):** logs in via your **external browser** — on the first tool call Nova opens the login page automatically and shows the device code as fallback. The token lives in server memory only, so you re-authorize once per Nova launch. The callback port is mapped to loopback only.
- **Personal Access Token (PAT):** set `env.GITHUB_PERSONAL_ACCESS_TOKEN` (inlined or via `${GITHUB_PAT}`); the token takes precedence over OAuth and no callback port is needed.

Other notes:

- `${VAR}` values in `env` are expanded from your environment at startup — keep secrets out of the file
- Tool risk is set per tool by Nova: names starting `get_/list_/search_/read_/fetch_` are read, `delete_/remove_` are destructive, everything else is write (asks for approval, with an *always allow* option). There is no per-server risk setting; GitHub's own read-only mode is available in the setup form.
- Each tool costs context in every prompt — enable only what you need

```bash
chmod 600 ~/.config/nova/mcp.json   # if you keep tokens inline
```
