import type { Message } from "@ag-ui/client";
import { MessageSchema } from "@ag-ui/core/schemas";
import type { Storage } from "./resume";
const LIMIT = 2 * 1024 * 1024;
export interface Conversation { threadId: string; messages: Message[] }
export function conversationKey(workspaceId: string, resumeId: string | null): string { return `avicv:chat:v1:${workspaceId}:${resumeId ?? "home"}`; }
export function loadConversation(key: string, storage?: Storage): Conversation {
  try {
    const raw = storage?.getItem(key);
    if (raw && raw.length <= LIMIT) {
      const value = JSON.parse(raw);
      if (/^[a-zA-Z0-9_-]{1,128}$/.test(value.threadId) && Array.isArray(value.messages)) {
        const parsed = MessageSchema.array().safeParse(value.messages);
        if (parsed.success) return { threadId: value.threadId, messages: parsed.data };
      }
    }
  } catch {}
  return { threadId: crypto.randomUUID(), messages: [] };
}
export function saveConversation(key: string, value: Conversation, storage?: Storage): void {
  try {
    const raw = JSON.stringify(value);
    if (raw.length <= LIMIT) storage?.setItem(key, raw);
  } catch { /* Browser storage can be unavailable; the current conversation still works. */ }
}
