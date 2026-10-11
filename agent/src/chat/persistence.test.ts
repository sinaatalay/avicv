import { expect, test } from "bun:test";
import { conversationKey, loadConversation, saveConversation } from "./persistence";
function storage() { const data = new Map<string, string>(); return { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } }; }
test("chat restores only the selected owner's CV conversation", () => {
  const local = storage(); const alice = conversationKey("alice", "resume");
  const saved = { threadId: "thread-id", messages: [{ id: "message", role: "user" as const, content: "My best matches" }] };
  saveConversation(alice, saved, local);
  expect(loadConversation(alice, local)).toEqual(saved);
  expect(loadConversation(conversationKey("bob", "resume"), local).messages).toEqual([]);
  expect(loadConversation(conversationKey("alice", "other"), local).messages).toEqual([]);
});
test("corrupt storage and forged thread namespaces cannot be restored", () => {
  const local = storage();
  for (const bad of ["junk", JSON.stringify({ threadId: "bob:private", messages: [] }), JSON.stringify({ threadId: "valid", messages: [{ role: "invalid" }] })]) {
    local.setItem("key", bad); const result = loadConversation("key", local);
    expect(result.messages).toEqual([]); expect(result.threadId).not.toBe("bob:private");
  }
  expect(() => saveConversation("key", { threadId: "t", messages: [] }, { getItem: () => null, setItem: () => { throw new Error("private browsing"); } })).not.toThrow();
});
