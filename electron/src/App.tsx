import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowUp,
  Blocks,
  Eraser,
  Settings,
  Square,
} from "lucide-react";
import { useNova } from "./hooks/useNova";
import { useSelectToCopy } from "./hooks/useSelectToCopy";
import { Artifact } from "./lib/artifacts";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { Btn } from "./components/Btn";
import { ChatBubble } from "./components/ChatBubble";
import { CopyToast } from "./components/CopyToast";
import { EmptyState } from "./components/EmptyState";
import { ThinkingText } from "./components/ThinkingText";
import { Inspector } from "./components/Inspector";
import { PermissionDialog } from "./components/PermissionDialog";
import { SettingsDialog } from "./components/SettingsDialog";
import { SkillsDialog } from "./components/SkillsDialog";
import { Sidebar } from "./components/Sidebar";

export default function App() {
  const {
    messages,
    input,
    setInput,
    busy,
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
  } = useNova();

  const [openedOutputs, setOpenedOutputs] = useState<Set<number>>(new Set());
  const [activeArtifact, setActiveArtifact] = useState<Artifact | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const copyToast = useSelectToCopy(listRef);

  // Grow the composer with its content so the internal scrollbar only
  // appears past the max height — not already on the first wrapped line.
  // border-box sizing means scrollHeight excludes the borders, so add them
  // back or the box renders 2px short and always overflows.
  useEffect(() => {
    const ta = composerRef.current;
    if (!ta) return;
    const cs = getComputedStyle(ta);
    const vBorder =
      parseFloat(cs.borderTopWidth || "0") + parseFloat(cs.borderBottomWidth || "0");
    const cap = parseFloat(cs.maxHeight) || 128;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, cap - vBorder) + vBorder + "px";
  }, [input]);

  const onChatKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  return (
<div className="flex h-screen flex-col bg-bg text-text antialiased select-none">
        {/* Statusline header */}
        <header className="flex h-9 items-center gap-4 border-b border-border bg-surface px-4 shrink-0">
          <div className="flex items-center gap-2.5">
            <span className="text-[12px] font-bold lowercase tracking-[0.28em] text-text">
              nova
            </span>
            <span
              className={`h-[6px] w-[6px] rounded-full ${
                spinner
                  ? "bg-warn animate-pulse"
                  : ollamaOk
                    ? "bg-ok shadow-[0_0_6px_rgba(110,231,160,0.8)]"
                    : "bg-danger"
              }`}
            />
            <span
              className={`text-mono text-[10px] tracking-[0.1em] ${
                spinner ? "text-meta" : ollamaOk ? "text-subtext" : "text-danger"
              }`}
            >
              {spinner ? "connecting…" : ollamaOk ? "online" : "offline"}
            </span>
            <Btn
              variant="plain"
              onClick={() => {
                clearChat();
                setActiveArtifact(null);
              }}
              title="Ctrl+Backspace"
            >
              <Eraser size={11} />
              Clear
            </Btn>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <div className="flex items-center gap-2 rounded-sm border border-border bg-surface-alt px-2 py-0.5">
              <span className="font-mono text-[10px] text-faint">model</span>
              <span className="font-mono text-[10px] text-accent-2">
                {spinner
                  ? "…"
                  : ollamaOk
                    ? modelInfo.name || "—"
                    : "offline"}
              </span>
            </div>
            <select
              className="h-6 max-w-[260px] cursor-pointer rounded-sm border border-border bg-surface-alt px-1.5 font-mono text-[10.5px] leading-none text-subtext outline-none focus:border-accent disabled:opacity-40"
              value={String(modelInfo.id || "")}
              disabled={!ollamaOk || models.length === 0}
              onChange={(e) => selectModel(e.target.value)}
              title={modelInfo.name ? `Model: ${modelInfo.name}` : "Model"}
            >
            {models.length === 0 && <option value="">models…</option>}
            {modelInfo.id && !models.includes(String(modelInfo.id)) && (
              <option value={String(modelInfo.id)}>{String(modelInfo.id)}</option>
            )}
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <Btn
            variant="plain"
            onClick={() => setSkillsOpen(true)}
            title="Skills"
          >
            <Blocks size={11} />
            Skills
          </Btn>
          <Btn
            variant="plain"
            onClick={() => setSettingsOpen(true)}
            title="Settings"
          >
            <Settings size={11} />
            Settings
          </Btn>
          <Btn
            variant="plain"
            onClick={() => setInspector((p) => !p)}
            title="Inspector · Ctrl+I"
          >
            <Activity size={11} />
            {inspector ? "Inspector ●" : "Inspector"}
          </Btn>
        </div>
      </header>

      {/* Main content */}
      <div className="flex min-h-0 flex-1">
        {/* Sidebar */}
        <Sidebar
          conversations={conversations}
          activeId={activeConversationId}
          messages={conversationMessages}
          onSelect={(id) => {
            switchConversation(id);
            setActiveArtifact(null);
          }}
          onNew={() => {
            newConversation();
            setActiveArtifact(null);
          }}
          onDelete={deleteConversation}
          onRename={renameConversation}
        />

        {/* Chat panel */}
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Messages */}
          <div ref={listRef} data-chat-scroll className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-5 pb-6 pt-5">
            {/* Error banner */}
            {error && (
              <div className="msg-in mb-4 rounded-sm border border-danger/40 bg-danger/10 px-4 py-2.5 font-mono text-xs text-danger">
                <div className="flex items-start justify-between gap-2">
                  <span>✗ {error}</span>
                  <button
                    className="cursor-pointer text-xs text-danger hover:underline"
                    onClick={() => window.location.reload()}
                  >
                    retry
                  </button>
                </div>
              </div>
            )}

            {/* Empty state */}
            {messages.length === 0 && !spinner && (
              <EmptyState
                ollamaOk={ollamaOk}
                modelCount={modelCount}
                onStarter={(text) => send(text)}
              />
            )}

            {/* Messages */}
            {messages.map((message, idx) => (
              <div key={idx}>
                <ChatBubble
                  message={message}
                  index={idx}
                  onCopy={(text) => navigator.clipboard.writeText(text)}
                  onOpenArtifact={(art) => setActiveArtifact(art)}
                  activeArtifactId={activeArtifact?.id}
                />
                {/* Open output button for tool results */}
                {message.role === "assistant" &&
                  message.tools.length > 0 &&
                  idx > 0 &&
                  (() => {
                    const lastToolIdx = message.tools.length - 1;
                    const tool = message.tools[lastToolIdx];
                    const ready =
                      message.tools.length > 0 &&
                      message.tools.every(
                        (t) => t.status === "ok" || t.status === "fail"
                      );
                    const noError = !message.tools.some(
                      (t) => t.status === "fail"
                    );
                    const output = tool?.outputPath;
                    const opened = openedOutputs.has(idx);
                    if (!ready || noError || !output) return null;
                    if (opened) return null;
                    return (
                      <button
                        key="open-output"
                        className="msg-in mb-4 ml-[3.5ch] cursor-pointer self-start rounded-sm border border-border bg-surface-alt px-2.5 py-1 font-mono text-[11px] text-accent hover:border-accent hover:text-accent-2"
                        onClick={() => {
                          window.nova.call("open", { path: output });
                          setOpenedOutputs((prev) => new Set([...prev, idx]));
                        }}
                      >
                        {message.tools.length > 1
                          ? "open output →"
                          : "open image →"}
                      </button>
                    );
                  })()}
              </div>
            ))}

            {/* Thinking / tool-run indicator — only when no streaming
                message is already rendering its own status */}
            {(busy || streaming) && !messages.some((m) => m.streaming) && (
              <div className="msg-in mb-4">
                <div className="text-mono text-[11px] leading-none">
                  <b className="font-bold tracking-[0.08em] text-accent">
                    nova
                  </b>
                  <span className="text-faint"> ›</span>
                </div>
                <div className="mt-1.5 pl-[3.5ch]">
                  <ThinkingText />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Composer: terminal input line */}
          <div className="border-t border-border bg-surface px-5 pb-3.5 pt-3 shrink-0">
            <div className="flex items-end gap-2.5">
              <span className="pb-2 font-mono text-[13px] font-bold text-accent select-none">
                ›
              </span>
              <textarea
                ref={composerRef}
                className="min-h-8 max-h-32 flex-1 resize-none overflow-y-auto rounded-none border-0 border-b border-border bg-transparent px-0.5 pb-1.5 pt-1 font-mono text-[12.5px] text-text outline-none transition-colors placeholder:text-faint focus:border-accent"
                placeholder={""}
                rows={1}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onChatKey}
                disabled={busy}
              />
              {busy || streaming ? (
                <Btn
                  variant="ghost"
                  onClick={() => window.nova.call("abort")}
                >
                  <Square size={10} fill="currentColor" />
                  Stop
                </Btn>
              ) : (
                <button
                  className="flex h-7 shrink-0 cursor-pointer items-center justify-center rounded-sm bg-accent px-3 text-accent-text transition-shadow hover:shadow-[0_0_14px_rgba(138,230,140,0.45)] disabled:cursor-default disabled:bg-disabled disabled:text-bg disabled:shadow-none"
                  disabled={!input.trim()}
                  onClick={() => send(input)}
                  title="Send (Ctrl+Enter)"
                >
                  <ArrowUp size={13} strokeWidth={2.5} />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Artifact side panel */}
        {activeArtifact && (
          <ArtifactPanel
            artifact={activeArtifact}
            onClose={() => setActiveArtifact(null)}
          />
        )}

        {/* Inspector */}
        {inspector && (
          <div className="w-[260px] shrink-0 border-l border-border bg-surface p-3">
            <Inspector
              modelInfo={modelInfo}
              response={response}
              system={system}
            />
          </div>
        )}
      </div>

      {/* Footer statusline */}
      <div className="flex h-5 items-center justify-between border-t border-border bg-surface px-4 shrink-0">
        <div className="font-mono text-[9px] tracking-[0.06em] text-faint">
          ctrl+i inspector · ctrl+enter send · ctrl+backspace clear · esc stop
        </div>
        <div className="font-mono text-[9px] tracking-[0.06em] text-faint">
          {messages.length > 0 ? `${messages.length} msgs` : ""}
        </div>
      </div>

      {/* Modals */}
      <SettingsDialog
        open={settingsOpen}
        settings={settings}
        onSave={(s) => {
          setSettings(s);
          setSettingsOpen(false);
          window.nova.call("settings_save", {
            settings_json: JSON.stringify(s),
          });
        }}
        onClose={() => setSettingsOpen(false)}
      />
      <SkillsDialog
        open={skillsOpen}
        skills={skills}
        onToggle={(name, enabled) => {
          setSkills((s) =>
            s.map((skill) =>
              skill.name === name ? { ...skill, enabled } : skill
            )
          );
          window.nova.call("toggle_skill", { name, enabled });
        }}
        onClose={() => setSkillsOpen(false)}
      />
      <PermissionDialog
        request={permissionRequest}
        onRespond={(approved, remember) => {
          window.nova.call("permission_respond", {
            tool: permissionRequest!.tool,
            approved,
            remember,
          });
          setPermissionRequest(null);
        }}
      />
      <CopyToast text={copyToast} />
    </div>
  );
}
