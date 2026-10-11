import { expect, test } from "bun:test";
import { admitChat, reserveChat } from "./admission";
import { loadConfig } from "./config";
const session = { workspaceId: "owner", cookie: `avicv_session=${"s".repeat(32)}` };
test("one workspace cannot occupy the process and global live capacity is bounded", () => {
  const first = reserveChat("limit-a")!;
  try {
    expect(reserveChat("limit-a")).toBeNull();
    const rest = ["limit-b", "limit-c", "limit-d"].map((id) => reserveChat(id)!);
    try { expect(rest.every(Boolean)).toBe(true); expect(reserveChat("limit-e")).toBeNull(); }
    finally { rest.forEach((lease) => lease.release()); }
  } finally { first.release(); first.release(); }
  const later = reserveChat("limit-a"); expect(later).not.toBeNull(); later?.release();
});
test("daily admission forwards only scoped identity, preserves quota retry, and fails closed", async () => {
  let sent: RequestInit | undefined;
  const limited = await admitChat(loadConfig({}), session, async (_url, init) => { sent = init; return new Response(null, { status: 429, headers: { "Retry-After": "3600" } }); });
  expect(limited?.status).toBe(429); expect(limited?.headers.get("Retry-After")).toBe("3600");
  expect(new Headers(sent?.headers).get("Cookie")).toBe(session.cookie); expect(new Headers(sent?.headers).get("Origin")).toBe("http://localhost:5173"); expect(sent?.redirect).toBe("error");
  expect((await admitChat(loadConfig({}), session, async () => { throw new Error("offline"); }))?.status).toBe(503);
});
