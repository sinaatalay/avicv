import { expect, test } from "bun:test";
import { loadConfig } from "../config";
import { createAvicvProxy } from "./avicv-proxy";
const id = "a".repeat(32); const artifact = "b".repeat(32); const cookie = `avicv_session=${"s".repeat(32)}`;
const config = loadConfig({ API_URL: "http://api.test" });
test("CV reads, polling and uploads forward only the browser credential", async () => {
  const calls: { url: string; headers: Headers; method: string }[] = [];
  const proxy = createAvicvProxy(config, async (url, init) => { calls.push({ url, headers: new Headers(init?.headers), method: init?.method ?? "GET" }); return Response.json({ ok: true }); });
  for (const path of ["resumes", `resumes/${id}`, `resumes/${id}/time-savings`, `resumes/${id}/tailored/${artifact}`]) {
    const res = await proxy(new Request(`http://app.test/chat/avicv/${path}`, { headers: { Cookie: `other=secret; ${cookie}` } }));
    expect(res?.status).toBe(200);
    expect(calls.at(-1)?.headers.get("Cookie")).toBe(cookie);
    expect(calls.at(-1)?.url).toBe(`http://api.test/${path}`);
  }
  const res = await proxy(new Request("http://app.test/chat/avicv/resumes", { method: "POST", headers: { Cookie: cookie, "Content-Type": "application/pdf" }, body: "%PDF" }));
  expect(res?.status).toBe(200); expect(calls.at(-1)?.method).toBe("POST");
});
test("unauthorized, arbitrary and oversized requests never reach upstream", async () => {
  let count = 0; const proxy = createAvicvProxy(config, async () => { count++; return new Response(); });
  expect((await proxy(new Request(`http://x/chat/avicv/resumes/${id}`)))?.status).toBe(401);
  expect((await proxy(new Request("http://x/chat/avicv/auth/tokens", { headers: { Cookie: cookie } })))?.status).toBe(404);
  expect((await proxy(new Request("http://x/chat/avicv/resumes", { method: "POST", headers: { Cookie: cookie }, body: new Uint8Array(11 * 1024 * 1024 + 1) })))?.status).toBe(413);
  expect(count).toBe(0);
});
