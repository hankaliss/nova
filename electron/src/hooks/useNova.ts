import { useState, useRef, useEffect } from "react";
import { Message, ToolEvent, Settings } from "../lib/types";

let nextId = 1;

function makeMsg(
  partial: Omit<Message, "id">
): Message {
  return { id: nextId++, ...partial };
}

function makeTool(partial: Omit<ToolEvent, "durationMs" | "error" | "outputPath">): ToolEvent {
  return { ...partial, durationMs: 0, error: "", outputPath: undefined };
}

export function useNova() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [inspector, setInspector] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [skills, setSkills] = useState<any[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [modelInfo, setModelInfo] = useState<Record<string, string | number>>(
    {}
  );
  const [response, setResponse] = useState<Record<string, string | number>>(
    {}
  );
  const [system, setSystem] = useState<Record<string, any>>({});
  const [ollamaOk, setOllamaOk] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [modelCount, setModelCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [permissionRequest, setPermissionRequest] = useState<{
    tool: string;
    risk: string;
    permission: string;
    args: string;
  } | null>(null);
  const [spinner, setSpinner] = useState(true);
  const [connected, setConnected] = useState(false);
  const [conversations, setConversations] = useState<any[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const [conversationMessages, setConversationMessages] = useState<Record<number, string[]>>({});
  const bottomRef = useRef<HTMLDivElement>(null);
  const pendingLoadRef = useRef(true);
  const activeIdRef = useRef<number | null>(null);

  useEffect(() => {
    activeIdRef.current = activeConversationId;
  }, [activeConversationId]);

  useEffect(() => {
    if (connected) {
      window.nova.call("settings");
      window.nova.call("system");
      window.nova.call("skills");
      window.nova.call("listConversations");
    }
  }, [connected]);

  // Keep the Inspector live: refresh system metrics immediately when it
  // opens, then poll while it stays open (sidecar only pushes once at
  // startup — without this, GPU/VRAM/RAM freeze at launch-time values).
  useEffect(() => {
    if (!inspector || !connected) return;
    window.nova.call("system");
    const id = window.setInterval(() => {
      window.nova.call("system");
    }, 3000);
    return () => window.clearInterval(id);
  }, [inspector, connected]);

  useEffect(() => {
    const unsubs: (() => void)[] = [];
    const unwrap = (params: any) =>
      Array.isArray(params) && params.length === 1 ? params[0] : params;
    const on = (event: string, cb: (data: any) => void) => {
      const unsub = window.nova.onEvent?.(event, (params: any) => cb(unwrap(params)));
      if (unsub) unsubs.push(unsub);
    };

    on("connected", () => setConnected(true));
    on("system", (data) => setSystem(data));
    on("skill_list", (data) => setSkills(data));
    on("model_list", (data) => {
      setModels(data);
      setModelCount(data.length);
    });
    on("status", (data) => {
      setOllamaOk(data.ok);
      setSpinner(false);
    });
    on("model_info", (data) => {
      const p: Record<string, string | number> = {};
      for (const k of [
        "family",
        "parameter_size",
        "quantization",
        "context_length",
      ])
        p[k] = data.details?.[k] ?? "";
      const name: string = data.model || "";
      if (name) {
        p.name = name;
        p.id = name;
      }
      if (data.details?.families) p.family = data.details.families[0] ?? "";
      if (data.context_length) p.context_length = data.context_length;
      setModelInfo(p);
    });
    on("settings", (data) => setSettings(data));
    on("response", (data) => {
      setResponse(data);
      setBusy(false);
    });
    on("permission", (data) => setPermissionRequest(data));
    on("error", (data) => {
      setError(data.error);
      setBusy(false);
      setSpinner(false);
    });
    on("conversation_list", (data) => {
      setConversations(data || []);
      if (pendingLoadRef.current && data.length > 0) {
        pendingLoadRef.current = false;
        window.nova.call("switchConversation", { conversation_id: data[0].id });
      }
    });
    on("conversationSwitched", (data) => {
      setActiveConversationId(data);
    });
    on("conversationDeleted", (data) => {
      setConversations((prev) => prev.filter((c) => c.id !== data));
      if (activeIdRef.current === data) {
        pendingLoadRef.current = true;
        setActiveConversationId(null);
        setMessages([]);
      }
    });
    on("messageAdded", (data) => {
      const [role, content, streaming] = data;
      if (content || streaming) {
        setMessages((m) => [
          ...m,
          makeMsg({
            role,
            content,
            streaming,
            tools: [],
          }),
        ]);
      }
    });

    return () => unsubs.forEach((u) => u());
  }, []);

  useEffect(() => {
    if (!spinner && !busy) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, busy, spinner]);

  const clearChat = () => {
    setMessages([]);
    setBusy(false);
    window.nova.call("clear");
  };

  const send = (text: string) => {
    const t = text.trim();
    if (!t || busy) return;
    setInput("");
    setBusy(true);
    setResponse({});
    // NOTE: no optimistic user bubble here — the backend emits
    // messageAdded("user", ...) as the single source of truth; an
    // optimistic copy duplicated every sent message in the chat.
    window.nova.call("send", { text: t });
  };

  const regenerate = (idx: number) => {
    if (busy) return;
    const lastUser = [...messages.slice(0, idx)]
      .reverse()
      .find((m) => m.role === "user");
    if (!lastUser) return;
    const userIdx =
      idx - 1 -
      [...messages.slice(0, idx)].reverse().findIndex((m) => m.role === "user");
    // Slice up to (not including) the user message: the backend re-emits
    // messageAdded("user", ...) on the re-send, so keeping the old copy
    // would duplicate it.
    setMessages((m) => m.slice(0, userIdx));
    setBusy(true);
    setResponse({});
    window.nova.call("send", { text: lastUser.content });
  };

  const selectModel = (name: string) => {
    if (!name || name === modelInfo.id) return;
    window.nova.call("selectModel", { name });
  };

  useEffect(() => {
    const down = (e: KeyboardEvent) => {};
    const up = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        if (streaming) {
          window.nova.call("abort");
        } else {
          send(input);
        }
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "Backspace") {
        e.preventDefault();
        clearChat();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "i") {
        setInspector((p) => !p);
      }
    };
    document.addEventListener("keydown", down);
    document.addEventListener("keyup", up);
    return () => {
      document.removeEventListener("keydown", down);
      document.removeEventListener("keyup", up);
    };
  }, [busy, streaming, input]);

  useEffect(() => {
    const unsubs: (() => void)[] = [];
    const unwrap = (params: any) =>
      Array.isArray(params) && params.length === 1 ? params[0] : params;
    const on = (event: string, cb: (data: any) => void) => {
      const unsub = window.nova.onEvent?.(event, (params: any) => cb(unwrap(params)));
      if (unsub) unsubs.push(unsub);
    };

    on("tool_progress", (data) => {
      if (data?.error) {
        setMessages((m) => {
          const next = [...m];
          let li = -1;
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].role === "assistant") {
              li = i;
              break;
            }
          }
          if (li < 0) return m;
          next[li] = { ...next[li], content: next[li].content + "\n\n" + data.error };
          return next;
        });
        return;
      }

      setMessages((m) => {
        const next = [...m];
        let last = [...next].reverse().find((x) => x.role === "assistant");
        if (!last) {
          last = makeMsg({
            role: "assistant",
            content: "",
            streaming: false,
            tools: [],
          });
          next.push(last);
        }
        const tools = [...last.tools];
        let target = tools.find((t) => t.name === data.name);
        if (!target) {
          target = makeTool({
            name: data.name,
            args: data.args || "",
            status: "running",
          });
          tools.push(target);
        }
        target.status = data.status;
        if (data.status === "ok" || data.status === "fail")
          target.durationMs = data.duration_ms;
        if (data.error) target.error = data.error;
        target.outputPath = data.output_path;
        const final = [
          ...next.slice(0, next.length - 1),
          { ...last, tools },
        ];
        setBusy(
          tools.some((t) => t.status === "running") || streaming
        );
        return final;
      });
    });

    return () => unsubs.forEach((u) => u());
  }, [streaming]);

  useEffect(() => {
    const unsubs: (() => void)[] = [];
    const unwrap = (params: any) =>
      Array.isArray(params) && params.length === 1 ? params[0] : params;
    const on = (event: string, cb: (data: any) => void) => {
      const unsub = window.nova.onEvent?.(event, (params: any) => cb(unwrap(params)));
      if (unsub) unsubs.push(unsub);
    };

    on("chunk", (data) => {
      const tok: string = data?.content;
      if (!tok) return;
      setStreaming(true);
      setMessages((m) => {
        const next = [...m];
        // Find the last assistant by INDEX (not by "last element") —
        // replacing next[length-1] corrupts history when the streaming
        // assistant is not the final entry.
        let ti = -1;
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === "assistant") {
            ti = i;
            break;
          }
        }
        const target = ti >= 0 && next[ti].streaming ? next[ti] : null;
        if (target) {
          next[ti] = { ...target, content: target.content + tok };
          return next;
        }
        return [
          ...next,
          makeMsg({
            role: "assistant",
            content: tok,
            streaming: true,
            tools: [],
          }),
        ];
      });
      setTimeout(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      }, 0);
    });

    return () => unsubs.forEach((u) => u());
  }, [streaming]);

  useEffect(() => {
    const unsubs: (() => void)[] = [];
    const unwrap = (params: any) =>
      Array.isArray(params) && params.length === 1 ? params[0] : params;
    const on = (event: string, cb: (data: any) => void) => {
      const unsub = window.nova.onEvent?.(event, (params: any) => cb(unwrap(params)));
      if (unsub) unsubs.push(unsub);
    };

    on("reply_done", () => {
      setStreaming(false);
      setBusy(false);
      setMessages((m) => {
        const next = [...m];
        const idx = next.length - 1;
        if (next[idx]?.streaming) {
          next[idx] = { ...next[idx], streaming: false };
        }
        return next;
      });
    });

    return () => unsubs.forEach((u) => u());
  }, [streaming]);

  const setOutputFor = (idx: number, toolIdx: number, outputPath: string) => {
    setMessages((m) => {
      const next = [...m];
      const msg = next[idx];
      if (!msg) return m;
      const tools = [...msg.tools];
      const tool = tools[toolIdx];
      if (!tool) return m;
      tools[toolIdx] = { ...tool, outputPath };
      next[idx] = { ...msg, tools };
      return next;
    });
  };

  const newConversation = () => {
    setMessages([]);
    setActiveConversationId(null);
    pendingLoadRef.current = true;
    window.nova.call("clear");
  };

  const switchConversation = (id: number) => {
    if (busy) return;
    setActiveConversationId(id);
    setMessages([]);
    window.nova.call("switchConversation", { conversation_id: id });
    // Reload the conversation list to get fresh data
    window.nova.call("listConversations");
  };

  const deleteConversation = (id: number) => {
    window.nova.call("deleteConversation", { conversation_id: id });
    if (activeConversationId === id) {
      setActiveConversationId(null);
      setMessages([]);
    }
  };

  const renameConversation = (id: number, title: string) => {
    window.nova.call("renameConversation", { conversation_id: id, title });
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title } : c))
    );
  };

  return {
    messages,
    setMessages,
    input,
    setInput,
    busy,
    setBusy,
    streaming,
    inspector,
    setInspector,
    settingsOpen,
    setSettingsOpen,
    skillsOpen,
    setSkillsOpen,
    skills,
    setSkills,
    settings,
    setSettings,
    modelInfo,
    response,
    system,
    ollamaOk,
    models,
    modelCount,
    error,
    permissionRequest,
    setPermissionRequest,
    spinner,
    connected,
    bottomRef,
    clearChat,
    send,
    regenerate,
    selectModel,
    setOutputFor,
    conversations,
    activeConversationId,
    conversationMessages,
    newConversation,
    switchConversation,
    deleteConversation,
    renameConversation,
  };
}