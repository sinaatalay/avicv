import { expect, test } from "bun:test";
import { loadConfig } from "../config";
import { createChatHandler } from "./server";
const config = loadConfig({ APP_ORIGIN: "http://app.test:5173" });
const session = { workspaceId: "owner", cookie: `avicv_session=${"s".repeat(32)}` };
const handler = createChatHandler(config);
test("chat config contains only public connection state, not credentials", async () => {
  const res = await handler(new Request("http://agent/chat/config.json"), session);
  expect(await res.json()).toEqual({ appOrigin: "http://app.test:5173", workspaceId: "owner", chatConfigured: false });
  expect(res.headers.get("cache-control")).toBe("private, no-store");
  expect(res.headers.get("content-security-policy")).toBe("frame-ancestors http://app.test:5173");
});
test("asset paths cannot read arbitrary files", async () => {
  expect((await handler(new Request("http://agent/chat/%2e%2e%2fpackage.json"), session)).status).toBe(404);
  expect((await handler(new Request("http://agent/chat/not-a-real-file.js"), session)).status).toBe(404);
});
