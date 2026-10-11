import { chatConfigured, loadConfig, type Config } from "./config";
import { authenticate, hasTrustedOrigin, type Fetch, type Session } from "./auth";
import { createChatHandler } from "./chat/server";
import { RunAgentInputSchema } from "@ag-ui/core/schemas";
import { admitChat, reserveChat, type ChatLease } from "./admission";
import { createCopilotHandler } from "./runtime";

export type Handler = (req: Request) => Response | Promise<Response>;
export interface ServerDeps {
  copilot?: (req: Request, session: Session, reservation?: ChatLease) => Response | Promise<Response>;
  chat?: (req: Request, session: Session) => Response | Promise<Response>;
  fetch?: Fetch;
}

export function createServer(config: Config, deps: ServerDeps = {}) {
  return Bun.serve({
    hostname: "127.0.0.1", port: config.port, maxRequestBodySize: 11 * 1024 * 1024,
    async fetch(req) {
      const { pathname } = new URL(req.url);
      const under = (prefix: string) => pathname === prefix || pathname.startsWith(`${prefix}/`);
      if (pathname === "/health" && req.method === "GET") return Response.json({ status: "ok" });
      if (!under("/chat") && !under("/api/copilotkit")) return Response.json({ error: "not found" }, { status: 404 });
      if (!["GET", "HEAD"].includes(req.method) && !hasTrustedOrigin(req, config)) return Response.json({ error: "forbidden" }, { status: 403 });
      const session = await authenticate(req, config, deps.fetch);
      if (session instanceof Response) return session;
      if (under("/chat") && deps.chat) return deps.chat(req, session);
      // Deliberately expose only the operations used by our chat. Generic inspector,
      // global thread listing/clear, memory and arbitrary resource endpoints are private.
      const runtimePath = pathname.slice("/api/copilotkit".length);
      const allowed = (req.method === "GET" && runtimePath === "/info") ||
        (req.method === "POST" && /^\/agent\/default\/(run|connect|stop\/[a-zA-Z0-9_-]{1,128})$/.test(runtimePath));
      if (!allowed || !deps.copilot) return Response.json({ error: "not found" }, { status: 404 });
      if (!chatConfigured(config)) return Response.json({ error: "Chat is not configured yet. You can still find jobs and tailor CVs in AviCV." }, { status: 503 });
      if (runtimePath !== "/agent/default/run") return deps.copilot(req, session);
      // Validate before spending a daily allowance or creating a model request.
      try {
        const raw = await req.clone().text();
        if (raw.length > 256 * 1024) return Response.json({ error: "Conversation is too long. Start a new chat." }, { status: 413 });
        const input = RunAgentInputSchema.parse(JSON.parse(raw));
        if (!/^[a-zA-Z0-9_-]{1,128}$/.test(input.threadId)) throw new Error("Invalid thread");
      } catch { return Response.json({ error: "Invalid chat request" }, { status: 400 }); }
      const reservation = reserveChat(session.workspaceId);
      if (!reservation) return Response.json({ error: "Please wait for the current reply." }, { status: 429, headers: { "Retry-After": "5" } });
      try {
        const denied = await admitChat(config, session, deps.fetch);
        if (denied) return denied;
        return await deps.copilot(req, session, reservation);
      } finally {
        // The runner takes over the lease synchronously when it starts its hot
        // event stream, and releases it at model completion (even after disconnect).
        if (!reservation.started) reservation.release();
      }
    },
  });
}

if (import.meta.main) {
  const config = loadConfig();
  const chat = createChatHandler(config);
  const server = createServer(config, {
    chat, copilot: (req, session, reservation) => createCopilotHandler(config, session, reservation)(req),
  });
  console.log(`AviCV chat listening on ${server.url}`);
}
