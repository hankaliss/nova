import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowUp,
  Blocks,
  Brain,
  Maximize2,
  Minimize2,
  Plug,
  Settings,
  Square,
  SquarePen,
  SquareTerminal,
} from "lucide-react";
import { useNova } from "./hooks/useNova";
import { useSelectToCopy } from "./hooks/useSelectToCopy";
import { Artifact, indexArtifacts } from "./lib/artifacts";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { Btn } from "./components/Btn";
import { ChatBubble } from "./components/ChatBubble";
import { ChoiceCard } from "./components/ChoiceCard";
import { CopyToast } from "./components/CopyToast";
import { EmptyState } from "./components/EmptyState";
import { ActivityStrip } from "./components/ActivityStrip";
import { extractChoices } from "./lib/choices";
import { extractSkill } from "./lib/skillDetect";
import { Inspector } from "./components/Inspector";
import { McpDialog } from "./components/McpDialog";
import { McpAuthDialog } from "./components/McpAuthDialog";
import { MemoriesDialog } from "./components/MemoriesDialog";
import { ModelPicker } from "./components/ModelPicker";
import { NovaMark } from "./components/NovaMark";
import { PermissionDialog } from "./components/PermissionDialog";
import { SettingsDialog } from "./components/SettingsDialog";
import { SetupStatus, SetupWizard } from "./components/SetupWizard";
import { SkillSaveCard } from "./components/SkillSaveCard";
import { SkillsDialog } from "./components/SkillsDialog";
import { Sidebar } from "./components/Sidebar";
import { TerminalPanel } from "./components/TerminalPanel";

export default function App() {
  const {
    messages,
    input,
    setInput,
    activity,
    busy,
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
    memories,
    addMemory,
    deleteMemory,
    mcpServers,
    authChallenge,
    dismissAuthChallenge,
    skills,
    setSkills,
    settings,
    setSettings,
    modelInfo,
    response,
    system,
    ollamaOk,
    modelCount,
    modelDetails,
    context,
    error,
    permissionRequest,
    setPermissionRequest,
    pendingChoices,
    selectChoice,
    spinner,
    connected,
    bottomRef,
    send,
    composerKeyDown,
    onComposerChange,
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
  // The open artifact, by id, resolved against this conversation's artifacts
  // (so it survives a message reload and can step through revisions).
  const [activeArtifactId, setActiveArtifactId] = useState<string | null>(null);
  const artifactIndex = useMemo(() => indexArtifacts(messages), [messages]);
  const activeArtifact = activeArtifactId
    ? artifactIndex.byId.get(activeArtifactId) ?? null
    : null;
  const [resolvedChoices, setResolvedChoices] = useState<Set<string>>(new Set());
  const [savedSkills, setSavedSkills] = useState<Set<string>>(new Set());
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const setupChecked = useRef(false);

  // First-run wizard (AD-040): once per launch, when setup was never
  // finished or Ollama has no models. The sidecar marks existing users done.
  useEffect(() => {
    if (!connected || spinner || setupChecked.current) return;
    setupChecked.current = true;
    void window.nova
      .call("getSetupStatus")
      .then((s: SetupStatus) => {
        if (s && (!s.onboarding_done || s.models.length === 0)) setSetupOpen(true);
      })
      .catch(() => {});
  }, [connected, spinner]);
  // Compact mode: small chat-only window (no sidebar, inspector or artifact
  // panel). Opening an artifact leaves it.
  // Remembered on this device across restarts.
  const [compact, setCompact] = useState(() => {
    try {
      return localStorage.getItem("nova.compact") === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    void window.nova.setCompact?.(compact);
    try {
      localStorage.setItem("nova.compact", compact ? "1" : "0");
    } catch {
      // storage unavailable: compact mode just isn't remembered
    }
  }, [compact]);
  // Each conversation remembers its open artifact (this device only).
  const artifactKey = (id: number | null) => (id === null ? null : `nova.artifact.${id}`);
  const selectArtifact = (id: string | null) => {
    setActiveArtifactId(id);
    const key = artifactKey(activeConversationId);
    if (!key) return;
    try {
      if (id) localStorage.setItem(key, id);
      else localStorage.removeItem(key);
    } catch {
      // storage unavailable: the panel just isn't remembered
    }
  };
  useEffect(() => {
    const key = artifactKey(activeConversationId);
    let stored: string | null = null;
    try {
      stored = key ? localStorage.getItem(key) : null;
    } catch {
      stored = null;
    }
    setActiveArtifactId(stored);
  }, [activeConversationId]);
  const openArtifact = (art: Artifact) => {
    setCompact(false);
    selectArtifact(art.id);
  };
  // When a reply finishes streaming with a new revision of the open artifact,
  // the panel moves to it. Replayed history never streams, so a revision the
  // user left open survives a restart or a conversation switch.
  const lastMessage = messages[messages.length - 1];
  // A skill's report (AD-039) opens as soon as it arrives in a reply. Only
  // while streaming: replayed history must not pop panels open. In compact
  // mode the badge is enough.
  const shownReports = useRef(new Set<string>());
  useEffect(() => {
    shownReports.current = new Set();
  }, [activeConversationId]);
  useEffect(() => {
    if (!lastMessage?.streaming) return;
    const fresh = (artifactIndex.byMessage.get(messages.length - 1) ?? []).filter(
      (a) => a.tool && !shownReports.current.has(a.id)
    );
    if (fresh.length === 0) return;
    fresh.forEach((a) => shownReports.current.add(a.id));
    if (!compact) selectArtifact(fresh[fresh.length - 1].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifactIndex]);
  const wasStreaming = useRef(false);
  useEffect(() => {
    const streaming = !!lastMessage?.streaming;
    const finished = wasStreaming.current && !streaming;
    wasStreaming.current = streaming;
    if (!finished || !activeArtifact) return;
    const fresh = artifactIndex.byMessage.get(messages.length - 1) ?? [];
    const revision = fresh.filter((a) => a.group === activeArtifact.group).pop();
    if (revision && revision.id !== activeArtifact.id) selectArtifact(revision.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastMessage?.streaming, artifactIndex]);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const copyToast = useSelectToCopy(listRef);

  // Index of the most recent assistant message — the only place a fallback
  // choice card may appear (detected from the model's text, choice.ts).
  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return i;
    }
    return -1;
  })();

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

  const onChatKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    composerKeyDown(e);
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  const startNewChat = () => {
    newConversation();
  };
  const startNewChatRef = useRef(startNewChat);
  startNewChatRef.current = startNewChat;

  // Ctrl+T toggles the shared shell terminal dock; Ctrl+N starts a new chat
  // (the previous conversation stays in the sidebar); Ctrl+M toggles compact
  // mode.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const key = e.key.toLowerCase();
      if (key === "t") {
        e.preventDefault();
        setTerminalOpen((open) => !open);
      } else if (key === "n") {
        e.preventDefault();
        startNewChatRef.current();
      } else if (key === "m") {
        e.preventDefault();
        setCompact((c) => !c);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const mcpAttention = mcpServers.some((s) => s.auth)
    ? "auth"
    : mcpServers.some((s) => s.state === "failed")
      ? "failed"
      : null;

  return (
    <div className="flex h-screen bg-bg text-text antialiased select-none">
      {/* Sidebar: brand, conversations, this machine */}
      {!compact && (
        <Sidebar
          conversations={conversations}
          activeId={activeConversationId}
          messages={conversationMessages}
          onSelect={(id) => {
            switchConversation(id);
          }}
          onNew={startNewChat}
          onDelete={deleteConversation}
          onRename={renameConversation}
          ollamaOk={ollamaOk}
          connecting={!!spinner}
          system={system}
          onOpenInspector={() => setInspector(true)}
        />
      )}

      <div className="flex min-w-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <h1 className="sr-only">Nova</h1>
          {/* Toolbar */}
          <header className="flex h-12 shrink-0 items-center gap-1 px-3">
            <ModelPicker
              models={modelDetails}
              current={String(modelInfo.name || "")}
              ollamaOk={ollamaOk}
              busy={!!spinner}
              onSelect={selectModel}
            />
            <div className="ml-auto flex shrink-0 items-center gap-0.5">
              {compact && (
                <Btn
                  variant="icon"
                  onClick={startNewChat}
                  title="New chat (Ctrl+N)"
                  ariaLabel="New chat"
                >
                  <SquarePen size={17} />
                </Btn>
              )}
              <Btn
                variant="icon"
                onClick={() => setSkillsOpen(true)}
                title="Skills"
                ariaLabel="Skills"
              >
                <Blocks size={17} />
              </Btn>
              <Btn
                variant="icon"
                onClick={() => setMemoriesOpen(true)}
                title="Memories"
                ariaLabel="Memories"
              >
                <Brain size={17} />
              </Btn>
              <Btn
                variant="icon"
                onClick={() => setMcpOpen(true)}
                title={
                  mcpAttention === "auth"
                    ? "MCP servers: a login is waiting"
                    : mcpAttention === "failed"
                      ? "MCP servers: one failed to start"
                      : "MCP servers"
                }
                ariaLabel="MCP servers"
                className="relative"
              >
                <Plug size={17} />
                {mcpAttention && (
                  <span
                    className={`absolute right-1.5 top-1.5 h-2 w-2 rounded-full ${
                      mcpAttention === "auth" ? "nova-pulse bg-warn" : "bg-danger"
                    }`}
                  />
                )}
              </Btn>
              {!compact && (
                <Btn
                  variant="icon"
                  onClick={() => setInspector((p) => !p)}
                  title="Inspector (Ctrl+I)"
                  ariaLabel="Inspector"
                  pressed={inspector}
                >
                  <Activity size={17} />
                </Btn>
              )}
              <Btn
                variant="icon"
                onClick={() => setTerminalOpen((p) => !p)}
                title="Terminal (Ctrl+T)"
                ariaLabel="Terminal"
                pressed={terminalOpen}
              >
                <SquareTerminal size={17} />
              </Btn>
              <Btn
                variant="icon"
                onClick={() => setSettingsOpen(true)}
                title="Settings"
                ariaLabel="Settings"
              >
                <Settings size={17} />
              </Btn>
              <Btn
                variant="icon"
                onClick={() => setCompact((c) => !c)}
                title={compact ? "Full window (Ctrl+M)" : "Compact mode (Ctrl+M)"}
                ariaLabel={compact ? "Full window" : "Compact mode"}
                pressed={compact}
              >
                {compact ? <Maximize2 size={17} /> : <Minimize2 size={17} />}
              </Btn>
            </div>
          </header>

          {/* Live "what is the model doing" strip */}
          <ActivityStrip
            activity={activity}
            model={String(modelInfo.name || "")}
          />

          {/* Messages */}
          <div
            ref={listRef}
            data-chat-scroll
            className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden"
          >
            <div className="mx-auto flex min-h-full w-full max-w-[780px] flex-col px-6 pb-8 pt-4">
              {/* Error banner */}
              {error && (
                <div
                  role="alert"
                  className="msg-in mb-5 flex items-start justify-between gap-3 rounded-xl border border-danger/40 bg-danger/10 px-4 py-3 text-[13.5px] text-danger"
                >
                  <span>{error}</span>
                  <button
                    className="shrink-0 cursor-pointer font-semibold hover:underline"
                    onClick={() => window.location.reload()}
                  >
                    Reload
                  </button>
                </div>
              )}

              {/* Empty state */}
              {messages.length === 0 && !spinner && (
                <EmptyState
                  ollamaOk={ollamaOk}
                  modelCount={modelCount}
                  model={String(modelInfo.name || "")}
                  onStarter={(text) => send(text)}
                  onOpenSetup={() => setSetupOpen(true)}
                />
              )}

              {/* Messages */}
              {messages.map((message, idx) => (
                <div key={idx}>
                  <ChatBubble
                    message={message}
                    index={idx}
                    onCopy={(text) => void window.nova.copyText(text)}
                    artifacts={artifactIndex.byMessage.get(idx) ?? []}
                    onOpenArtifact={openArtifact}
                    activeArtifactId={activeArtifact?.id}
                    onRegenerate={!busy && idx === lastAssistantIdx ? regenerate : undefined}
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
                          className="msg-in mb-5 ml-9 cursor-pointer self-start rounded-lg border border-border bg-surface px-3 py-1.5 text-[13px] text-subtext hover:border-accent/60 hover:text-text"
                          onClick={() => {
                            window.nova.call("open", { path: output });
                            setOpenedOutputs((prev) => new Set([...prev, idx]));
                          }}
                        >
                          {message.tools.length > 1
                            ? "Open output"
                            : "Open image"}
                        </button>
                      );
                    })()}
                  {/* Fallback choice card: the model wrote the options as
                      plain text instead of calling nova.presentChoices. Render
                      clickable buttons; picking one sends it as a reply. */}
                  {message.role === "assistant" &&
                    !message.streaming &&
                    idx === lastAssistantIdx &&
                    (() => {
                      const pick = extractChoices(message.content);
                      if (!pick) return null;
                      const key = `${message.id}:${pick.options.join("|")}`;
                      if (resolvedChoices.has(key)) return null;
                      return (
                        <ChoiceCard
                          choices={pick}
                          onSelect={(i) => {
                            setResolvedChoices((prev) => {
                              const next = new Set(prev);
                              next.add(key);
                              return next;
                            });
                            send(pick.options[i]);
                          }}
                        />
                      );
                    })()}
                  {/* Fallback "save skill" card: the model showed SKILL.md +
                      tools.py code blocks but never called nova.skill.create.
                      Offer a button that saves them. */}
                  {message.role === "assistant" &&
                    !message.streaming &&
                    idx === lastAssistantIdx &&
                    (() => {
                      const skill = extractSkill(message.content, message.tools);
                      if (!skill) return null;
                      const key = `${message.id}:${skill.skill_md.length}:${skill.tools_py.length}`;
                      if (savedSkills.has(key)) return null;
                      return (
                        <SkillSaveCard
                          skill={skill}
                          onSaved={(name) => {
                            setSavedSkills((prev) => {
                              const next = new Set(prev);
                              next.add(key);
                              return next;
                            });
                            setSkills((skills) =>
                              skills.map((s) =>
                                s.name === name
                                  ? { ...s, enabled: true, origin: "user" }
                                  : s
                              )
                            );
                          }}
                          onFix={(fixName, detail) =>
                            send(
                              `The skill "${fixName}" you just created is saved but its READ test failed: ${detail}. ` +
                                `Fix it: read the skill with nova.skill.get, correct its tools.py ` +
                                `(it must be self-contained — every name it uses must be imported or defined; ` +
                                `prefer subprocess over any magical shell object), then update it with ` +
                                `nova.skill.update and make sure the READ test passes.`
                            )
                          }
                        />
                      );
                    })()}
                </div>
              ))}

              {/* Inline multiple-choice card while the LLM waits for a selection */}
              {pendingChoices && (
                <ChoiceCard
                  choices={pendingChoices}
                  onSelect={selectChoice}
                />
              )}
              <div ref={bottomRef} />
            </div>
          </div>

          {/* Composer */}
          <div className="shrink-0 px-6 pb-4">
            <div className="mx-auto w-full max-w-[780px]">
              <div className="flex items-end gap-2 rounded-2xl border border-border bg-surface p-2 pl-4 shadow-[0_8px_30px_rgba(0,0,0,0.35)] transition-colors focus-within:border-accent/70">
                <textarea
                  ref={composerRef}
                  aria-label="Message Nova"
                  className="max-h-40 min-h-9 flex-1 resize-none overflow-y-auto border-0 bg-transparent py-2 text-[14.5px] leading-normal text-text outline-none placeholder:text-faint"
                  placeholder={
                    pendingChoices
                      ? "Pick an option above to continue"
                      : "Ask Nova anything…"
                  }
                  rows={1}
                  value={input}
                  onChange={(e) => onComposerChange(e.target.value)}
                  onKeyDown={onChatKey}
                  disabled={busy || !!pendingChoices}
                />
                {busy || streaming ? (
                  <Btn variant="ghost" onClick={() => window.nova.call("abort")} title="Stop (Esc)">
                    <Square size={11} fill="currentColor" />
                    Stop
                  </Btn>
                ) : (
                  <button
                    className="flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-xl bg-accent text-accent-text transition-colors hover:bg-accent-2 disabled:cursor-default disabled:bg-surface-alt disabled:text-faint"
                    disabled={!input.trim()}
                    onClick={() => send(input)}
                    title="Send (Enter)"
                    aria-label="Send"
                  >
                    <ArrowUp size={17} strokeWidth={2.4} />
                  </button>
                )}
              </div>
              <p className="mt-2 truncate px-1 text-center text-[12px] text-meta">
                Enter to send, Shift+Enter for a new line
              </p>
            </div>
          </div>

          {/* Terminal dock (shared shell session) */}
          {terminalOpen && <TerminalPanel onClose={() => setTerminalOpen(false)} />}
        </main>

        {/* Artifact side panel */}
        {activeArtifact && !compact && (
          <ArtifactPanel
            artifact={activeArtifact}
            versions={artifactIndex.groups.get(activeArtifact.group) ?? [activeArtifact]}
            onSelect={(art) => selectArtifact(art.id)}
            onClose={() => selectArtifact(null)}
          />
        )}

        {/* Inspector */}
        {inspector && !compact && (
          <aside className="w-[280px] shrink-0 overflow-y-auto border-l border-border bg-surface p-4">
            <Inspector
              modelInfo={modelInfo}
              response={response}
              system={system}
              context={context}
            />
          </aside>
        )}
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
        onRunSetup={() => {
          setSettingsOpen(false);
          setSetupOpen(true);
        }}
      />
      <SetupWizard
        open={setupOpen}
        settings={settings}
        onSaveSettings={(s) => {
          setSettings(s);
          window.nova.call("settings_save", { settings_json: JSON.stringify(s) });
        }}
        onClose={() => setSetupOpen(false)}
      />
      <MemoriesDialog
        open={memoriesOpen}
        memories={memories}
        onAdd={addMemory}
        onDelete={deleteMemory}
        onClose={() => setMemoriesOpen(false)}
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
      <McpDialog
        open={mcpOpen}
        servers={mcpServers}
        onToggle={(name, enabled) => {
          window.nova.call("mcpToggle", { name, enabled });
        }}
        onRestart={(name) => {
          window.nova.call("mcpRestart", { name });
        }}
        onSave={async (name, entry) => {
          try {
            await window.nova.call("mcpSave", { name, entry: JSON.stringify(entry) });
            return true;
          } catch (e) {
            console.error("mcpSave failed", e);
            return false;
          }
        }}
        onClose={() => setMcpOpen(false)}
      />
      <McpAuthDialog
        challenge={authChallenge}
        onDismiss={dismissAuthChallenge}
        onSignedIn={dismissAuthChallenge}
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
