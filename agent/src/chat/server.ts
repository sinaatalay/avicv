import { chatConfigured, type Config } from "../config";
import type { Session } from "../auth";
import { createAvicvProxy } from "./avicv-proxy";
import { resolve } from "node:path";

/** Assets are built before serving. The dev launcher runs the same build once. */
export function createChatHandler(config: Config, fetchFn?: typeof fetch) {
  const proxy = createAvicvProxy(config, fetchFn);
  const headers = { "Content-Security-Policy": `frame-ancestors ${config.appOrigin}`, "Cache-Control": "private, no-store" };
  const assetRoot = resolve(import.meta.dir, "../../dist/chat");
  return async (req: Request, session: Session) => {
    const proxied = await proxy(req);
    if (proxied) return proxied;
    const { pathname } = new URL(req.url);
    if (!["GET", "HEAD"].includes(req.method)) return new Response("method not allowed", { status: 405, headers });
    if (pathname === "/chat/config.json") return Response.json({ appOrigin: config.appOrigin, workspaceId: session.workspaceId, chatConfigured: chatConfigured(config) }, { headers });
    const rel = pathname === "/chat" || pathname === "/chat/" ? "index.html" : pathname.slice("/chat/".length);
    if (!/^[a-zA-Z0-9_.-]+$/.test(rel)) return new Response("not found", { status: 404, headers });
    const file = Bun.file(resolve(assetRoot, rel));
    if (!(await file.exists())) return new Response(rel === "index.html" ? "Chat is being prepared. Please try again shortly." : "not found", { status: rel === "index.html" ? 503 : 404, headers });
    const encodings = (req.headers.get("Accept-Encoding") ?? "").split(",").map((value) => value.trim().split(";"));
    for (const encoding of ["br", "gzip"] as const) {
      const accepted = encodings.find(([name]) => name === encoding);
      const quality = accepted?.find((part) => part.trim().startsWith("q="))?.trim().slice(2);
      if (!accepted || (quality !== undefined && Number(quality) <= 0)) continue;
      const compressed = Bun.file(resolve(assetRoot, `${rel}.${encoding === "br" ? "br" : "gz"}`));
      if (await compressed.exists()) return new Response(req.method === "HEAD" ? null : compressed, { headers: { ...headers, "Content-Type": file.type, "Content-Encoding": encoding, Vary: "Accept-Encoding" } });
    }
    return new Response(req.method === "HEAD" ? null : file, { headers: { ...headers, "Content-Type": file.type, Vary: "Accept-Encoding" } });
  };
}
