import { afterAll, expect, test } from "bun:test";
import { loadConfig } from "./config";
import { createServer } from "./server";
const cookie = `avicv_session=${"s".repeat(32)}`;
const calls: string[] = [];
const server = createServer(loadConfig({ PORT: "0", MODEL: "configured" }), {
  fetch: async (_url, init) => new Headers(init?.headers).get("Cookie") === cookie ? Response.json({ workspace_id: "owner1" }) : new Response("no", { status: 401 }),
  copilot: (_req, session) => { calls.push(session.workspaceId); return Response.json({ owner: session.workspaceId }); },
  chat: (_req, session) => Response.json({ owner: session.workspaceId }),
});
const runBody = JSON.stringify({ threadId: "thread", runId: "run", messages: [], tools: [], context: [], state: {}, forwardedProps: {} });
const base = `http://127.0.0.1:${server.port}`;
afterAll(() => server.stop(true));
test("health is public but chat/model endpoints require a backend-validated session", async () => {
  expect((await fetch(`${base}/health`)).status).toBe(200);
  expect((await fetch(`${base}/chat`)).status).toBe(401);
  expect((await fetch(`${base}/api/copilotkit/info`, { headers: { "X-Workspace-ID": "owner1" } })).status).toBe(401);
  expect((await fetch(`${base}/api/copilotkit/info`, { headers: { Cookie: cookie } })).status).toBe(200);
});
test("no duplicate MCP or global CopilotKit history/inspector endpoints", async () => {
  for (const path of ["/mcp", "/api/copilotkit/threads", "/api/copilotkit/inspector-metadata", "/api/copilotkit/trajectory/test/connect"]) {
    expect((await fetch(`${base}${path}`, { headers: { Cookie: cookie } })).status).toBe(404);
  }
});
test("cross-origin mutations fail before a model request", async () => {
  calls.length = 0;
  expect((await fetch(`${base}/api/copilotkit/agent/default/run`, { method: "POST", headers: { Cookie: cookie, Origin: "https://evil.example" }, body: runBody })).status).toBe(403);
  expect(calls).toHaveLength(0);
  expect((await fetch(`${base}/api/copilotkit/agent/default/run`, { method: "POST", headers: { Cookie: cookie, Origin: "http://localhost:5173" }, body: runBody })).status).toBe(200);
  expect(calls).toEqual(["owner1"]);
});

test("invalid runs do not spend allowance; busy and daily-denied runs return HTTP 429", async () => {
  let admissionCalls = 0; let runs = 0; let lease: import("./admission").ChatLease | undefined; let deny = false;
  const bounded = createServer(loadConfig({ PORT: "0", MODEL: "configured" }), {
    fetch: async (url) => {
      if (String(url).endsWith("/auth/session")) return Response.json({ workspace_id: "quota-owner" });
      admissionCalls++;
      return deny ? new Response(null, { status: 429, headers: { "Retry-After": "100" } }) : Response.json({ accepted: true });
    },
    copilot: (_request, _session, reservation) => { runs++; if (reservation) { lease = reservation; lease.started = true; } return new Response("ok"); },
  });
  const url = `http://127.0.0.1:${bounded.port}/api/copilotkit/agent/default/run`;
  const send = (body = runBody) => fetch(url, { method: "POST", headers: { Cookie: cookie, Origin: "http://localhost:5173" }, body });
  try {
    expect((await send("{}")).status).toBe(400); expect(admissionCalls).toBe(0);
    expect((await send()).status).toBe(200); expect(runs).toBe(1);
    const busy = await send(); expect(busy.status).toBe(429); expect(busy.headers.get("Retry-After")).toBe("5"); expect(admissionCalls).toBe(1);
    lease?.release(); deny = true;
    const daily = await send(); expect(daily.status).toBe(429); expect(daily.headers.get("Retry-After")).toBe("100"); expect(runs).toBe(1);
  } finally { lease?.release(); bounded.stop(true); }
});
