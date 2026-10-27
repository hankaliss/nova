import { useEffect, useState } from "react";
import { eventScope } from "../lib/events";

/** MCP server status and the pending login challenge (if any). */
export function useMcp() {
  const [mcpServers, setMcpServers] = useState<any[]>([]);
  const [authChallenge, setAuthChallenge] = useState<{
    server: string;
    url: string;
    code: string;
  } | null>(null);

  useEffect(() => {
    const { on, off } = eventScope();

    on("mcp_status", (data) => {
      const parsed =
        typeof data === "string"
          ? (() => {
              try {
                return JSON.parse(data);
              } catch {
                return data;
              }
            })()
          : data;
      const servers = Array.isArray(parsed) ? parsed : [];
      setMcpServers(servers);
      // Sync the login dialog with backend truth: open when a server has a
      // pending challenge (also restores it after a renderer reload), close
      // once that server's auth is cleared.
      setAuthChallenge((cur) => {
        const pending = servers.find((s: any) => s.auth);
        if (!pending) return null;
        if (cur && cur.server === pending.name) return cur;
        return {
          server: pending.name,
          url: pending.auth.url ?? "",
          code: pending.auth.code ?? "",
        };
      });
    });
    on("mcp_auth_required", (data) => {
      try {
        const info = typeof data === "string" ? JSON.parse(data) : data;
        if (info?.server)
          setAuthChallenge({
            server: String(info.server),
            url: String(info.url ?? ""),
            code: String(info.code ?? ""),
          });
      } catch {
        // malformed payload — status sync will correct state
      }
    });

    return off;
  }, []);

  return {
    mcpServers,
    authChallenge,
    dismissAuthChallenge: () => setAuthChallenge(null),
  };
}
