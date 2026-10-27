import { useEffect, useState } from "react";
import { Memory } from "../lib/types";
import { eventScope } from "../lib/events";

/** Facts the user asked Nova to remember (Memories panel). */
export function useMemories(connected: boolean) {
  const [memories, setMemories] = useState<Memory[]>([]);

  useEffect(() => {
    const { on, off } = eventScope();
    on("memory_list", (data) => setMemories(Array.isArray(data) ? data : []));
    return off;
  }, []);

  useEffect(() => {
    if (connected) window.nova.call("listMemories");
  }, [connected]);

  return {
    memories,
    addMemory: (content: string) => window.nova.call("addMemory", { content }),
    deleteMemory: (id: number) => window.nova.call("deleteMemory", { memory_id: id }),
  };
}
