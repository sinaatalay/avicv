import { expect, test } from "bun:test";
import { loadConfig } from "./config";
import { agentModel, avicvMcpFetch, createCopilotHandler } from "./runtime";
const session = { workspaceId: "owner", cookie: `avicv_session=${"s".repeat(32)}` };
test("missing model yields a clear error", () => { expect(() => createCopilotHandler(loadConfig({}), session)).toThrow("MODEL"); });
test("canonical MCP gets only this request's cookie and cannot redirect it", async () => {
  let seen: RequestInit | undefined;
  const fetchFn = (async (_input: unknown, init?: RequestInit) => { seen = init; return new Response("ok"); }) as typeof fetch;
  const send = avicvMcpFetch(loadConfig({}), session, fetchFn);
  await send("http://127.0.0.1:8000/mcp", { headers: { Authorization: "untrusted", Cookie: "other=secret" } });
  expect(new Headers(seen?.headers).get("Cookie")).toBe(session.cookie);
  expect(new Headers(seen?.headers).get("Authorization")).toBeNull();
  expect(seen?.redirect).toBe("error");
  await expect(send("https://mcp.darwin.so/mcp")).rejects.toThrow("Unexpected");
});
test("Anthropic configuration explicitly uses its own API key and workspace header", async () => {
  let headers: Headers | undefined;
  const fake = (async (_input: unknown, init?: RequestInit) => { headers = new Headers(init?.headers); return new Response("{}", { status: 400 }); }) as typeof fetch;
  const model = agentModel(loadConfig({ MODEL: "anthropic:claude-sonnet-5-5", ANTHROPIC_API_KEY: "test-only", ANTHROPIC_WORKSPACE_ID: "workspace-test" }), fake);
  await (model as any).doGenerate({ prompt: [{ role: "user", content: [{ type: "text", text: "hello" }] }] }).catch(() => {});
  expect(headers?.get("anthropic-workspace-id")).toBe("workspace-test");
  expect(headers?.get("x-api-key")).toBe("test-only");
});

test("OpenRouter selection uses Vertex without provider fallback and without any browser cookie", async () => {
  let headers: Headers | undefined; let body: any;
  const fake = (async (_input: unknown, init?: RequestInit) => { headers = new Headers(init?.headers); body = JSON.parse(String(init?.body)); return new Response("{}", { status: 400 }); }) as typeof fetch;
  const config = loadConfig({ OPENROUTER_API_KEY: "test-or-key" });
  expect(config.model).toBe("openrouter:google/gemini-3.8-flash");
  const model = agentModel(config, fake);
  await (model as any).doGenerate({ prompt: [{ role: "user", content: [{ type: "text", text: "hello" }] }] }).catch(() => {});
  expect(headers?.get("authorization")).toBe("Bearer test-or-key"); expect(headers?.get("cookie")).toBeNull();
  expect(body.provider).toEqual({ order: ["google-vertex"], allow_fallbacks: false });
  expect(body.model).toBe("google/gemini-3.8-flash");
});
test("an explicitly selected Anthropic model never silently switches to OpenRouter", () => {
  const config = loadConfig({ MODEL: "anthropic:claude-sonnet-5-5", OPENROUTER_API_KEY: "test-only" });
  expect(config.model).toBe("anthropic:claude-sonnet-5-5");
  expect(() => createCopilotHandler(config, session)).toThrow("API key");
});
