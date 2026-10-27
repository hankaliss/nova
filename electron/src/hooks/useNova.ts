import { useState, useRef, useEffect } from "react";
import {
  Message,
  PendingChoices,
  ToolEvent,
  Settings,
} from "../lib/types";
import { eventScope } from "../lib/events";
import { useActivity } from "./useActivity";
import { useComposerHistory } from "./useComposerHistory";
import { useConversations } from "./useConversations";
import { useMachine } from "./useMachine";
import { useMcp } from "./useMcp";
import { useMemories } from "./useMemories";

let nextId = 1;

function makeMsg(
  partial: Omit<Message, "id">
): Message {
  return { id: nextId++, created_at: Math.floor(Date.now() / 1000), ...partial };
}

function makeTool(partial: Omit<ToolEvent, "durationMs" | "error" | "outputPath">): ToolEvent {
  return { ...partial, durationMs: 0, error: "", outputPath: undefined };
}

/**
 * All renderer state wired to the sidecar. Chat state and events live here;
 * focused hooks own the rest (activity strip, composer history,
 * conversations, models/system metrics, MCP).
 */
export function useNova() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [inspector, setInspector] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [mcpOpen, setMcpOpen] = useState(false);
  const [skills, setSkills] = useState<any[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [response, setResponse] = useState<Record<string, string | number>>(
    {}
  );
  const [error, setError] = useState<string | null>(null);
  const [permissionRequest, setPermissionRequest] = useState<{
    tool: string;
    risk: string;
    permission: string;
    args: string;
  } | null>(null);
  const [pendingChoices, setPendingChoices] = useState<PendingChoices | null>(null);
  const [connected, setConnected] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const activity = useActivity();
  const history = useComposerHistory(input, setInput);
  const mcp = useMcp();
  const machine = useMachine(connected, inspector);
  const { spinner, setSpinner, modelInfo } = machine;
  const convs = useConversations(setMessages, busy);
  const memory = useMemories(connected);
  const [memoriesOpen, setMemoriesOpen] = useState(false);

  useEffect(() => {
    if (connected) {
      window.nova.call("settings");
      window.nova.call("system");
      window.nova.call("skills");
      window.nova.call("listConversations");
      window.nova.call("mcpStatus");
      window.nova.call("shortcutStatus");
    }
  }, [connected]);

  useEffect(() => {
    const { on, off } = eventScope();

    on("connected", () => setConnected(true));
    on("skill_list", (data) => setSkills(data));
    on("settings", (data) => setSettings(data));
    on("response", (data) => {
      setResponse(data);
      setBusy(false);
    });
    on("permission", (data) => setPermissionRequest(data));
    on("choices", (data) => {
      if (data?.question && Array.isArray(data?.options) && data.options.length >= 2) {
        setPendingChoices({
          question: String(data.question),
          options: data.options.map((o: unknown) => String(o)),
        });
      }
    });
    on("error", (data) => {
      setError(data.error);
      setBusy(false);
      setSpinner(false);
      setPendingChoices(null);
    });
    on("messageAdded", (data) => {
      const [role, content, streaming, created_at] = data;
      if (content || streaming) {
        setMessages((m) => [
          ...m,
          makeMsg({
            role,
            content,
            streaming,
            tools: [],
            created_at,
          }),
        ]);
      }
    });

    return off;
  }, []);

  useEffect(() => {
    if (!spinner && !busy) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, busy, spinner]);

  const send = (text: string) => {
    const t = text.trim();
    if (!t || busy) return;
    history.recordSent(t);
    history.endRecall();
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
    // The backend drops the old turn from the context and the saved chat,
    // then asks again (re-emitting the question).
    window.nova.call("regenerateLast");
  };

  const selectChoice = (index: number) => {
    // Dismiss immediately so the chat stays usable; the backend resolves the
    // pending tool result (or the abort path clears it on reply_done).
    setPendingChoices(null);
    window.nova.call("choice_select", { index });
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
      if ((e.metaKey || e.ctrlKey) && e.key === "i") {
        setInspector((p) => !p);
      }
      if (e.key === "Escape" && (busy || streaming)) {
        e.preventDefault();
        window.nova.call("abort");
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
    const { on, off } = eventScope();

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

    return off;
  }, [streaming]);

  useEffect(() => {
    const { on, off } = eventScope();

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

    return off;
  }, [streaming]);

  useEffect(() => {
    const { on, off } = eventScope();

    on("reply_done", () => {
      setStreaming(false);
      setBusy(false);
      setPendingChoices(null);
      setMessages((m) => {
        const next = [...m];
        const idx = next.length - 1;
        if (next[idx]?.streaming) {
          next[idx] = { ...next[idx], streaming: false };
        }
        return next;
      });
    });

    return off;
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

  return {
    messages,
    setMessages,
    activity,
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
    mcpOpen,
    setMcpOpen,
    memoriesOpen,
    setMemoriesOpen,
    memories: memory.memories,
    addMemory: memory.addMemory,
    deleteMemory: memory.deleteMemory,
    mcpServers: mcp.mcpServers,
    authChallenge: mcp.authChallenge,
    dismissAuthChallenge: mcp.dismissAuthChallenge,
    skills,
    setSkills,
    settings,
    setSettings,
    modelInfo,
    response,
    system: machine.system,
    ollamaOk: machine.ollamaOk,
    models: machine.models,
    modelCount: machine.modelCount,
    modelDetails: machine.modelDetails,
    context: machine.context,
    error,
    permissionRequest,
    setPermissionRequest,
    pendingChoices,
    selectChoice,
    spinner,
    connected,
    bottomRef,
    send,
    composerKeyDown: history.composerKeyDown,
    onComposerChange: history.onComposerChange,
    regenerate,
    selectModel: machine.selectModel,
    setOutputFor,
    conversations: convs.conversations,
    activeConversationId: convs.activeConversationId,
    conversationMessages: convs.conversationMessages,
    newConversation: convs.newConversation,
    switchConversation: convs.switchConversation,
    deleteConversation: convs.deleteConversation,
    renameConversation: convs.renameConversation,
  };
}
