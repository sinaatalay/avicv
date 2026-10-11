import { expect, test } from "bun:test";
import { authenticate, sessionCookie } from "./auth";
import { loadConfig } from "./config";
const cookie = `avicv_session=${"s".repeat(32)}`;
test("only the unique opaque session cookie is forwarded", () => {
  expect(sessionCookie(new Request("https://avicv.com/chat", { headers: { Cookie: `other=private; ${cookie}` } }))).toBe(cookie);
  expect(sessionCookie(new Request("https://avicv.com/chat", { headers: { Cookie: `${cookie}; ${cookie}` } }))).toBeNull();
});
test("workspace identity comes only from FastAPI session validation", async () => {
  let headers: Headers | undefined;
  const result = await authenticate(new Request("https://avicv.com/chat", { headers: { Cookie: cookie, "X-Workspace-Id": "victim" } }), loadConfig({}), async (_url, init) => {
    headers = new Headers(init?.headers); return Response.json({ workspace_id: "owner" });
  });
  expect(result).toEqual({ workspaceId: "owner", cookie });
  expect(headers?.get("X-Workspace-Id")).toBeNull();
  const denied = await authenticate(new Request("https://avicv.com/chat", { headers: { Cookie: cookie } }), loadConfig({}), async () => new Response(null, { status: 401 }));
  expect(denied instanceof Response && denied.status).toBe(401);
});
