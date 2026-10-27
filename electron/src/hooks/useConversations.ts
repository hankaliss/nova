import { useEffect, useRef, useState } from "react";
import { Message } from "../lib/types";
import { eventScope } from "../lib/events";

/** Sidebar conversations: list, active one, switch/new/delete/rename. */
export function useConversations(
  setMessages: (messages: Message[]) => void,
  busy: boolean
) {
  const [conversations, setConversations] = useState<any[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const [conversationMessages, setConversationMessages] = useState<Record<number, string[]>>({});
  const pendingLoadRef = useRef(true);
  const activeIdRef = useRef<number | null>(null);

  useEffect(() => {
    activeIdRef.current = activeConversationId;
  }, [activeConversationId]);

  useEffect(() => {
    const { on, off } = eventScope();

    on("conversation_list", (data) => {
      setConversations(data || []);
      if (pendingLoadRef.current && data.length > 0) {
        pendingLoadRef.current = false;
        window.nova.call("switchConversation", { conversation_id: data[0].id });
      }
    });
    // The sidecar replays the conversation's messages right after this
    // event, so start from an empty view. Without the clear, the startup
    // replay (load_history) plus the switch replay showed it twice.
    on("conversationSwitched", (data) => {
      setActiveConversationId(data);
      setMessages([]);
    });
    on("conversationDeleted", (data) => {
      setConversations((prev) => prev.filter((c) => c.id !== data));
      if (activeIdRef.current === data) {
        pendingLoadRef.current = true;
        setActiveConversationId(null);
        setMessages([]);
      }
    });

    return off;
  }, []);

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
    conversations,
    activeConversationId,
    conversationMessages,
    newConversation,
    switchConversation,
    deleteConversation,
    renameConversation,
  };
}
