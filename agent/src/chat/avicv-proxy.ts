import type { Config } from "../config";
import { sessionCookie } from "../auth";
const PREFIX = "/chat/avicv/";
const ID = "[0-9a-f]{32}";
const MAX_UPLOAD_BYTES = 11 * 1024 * 1024;
type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** Private, deliberately small REST adapter for cards/uploads; core work lives in FastAPI. */
export function createAvicvProxy(config: Config, fetchFn: Fetch = fetch) {
  const base = { "Content-Security-Policy": `frame-ancestors ${config.appOrigin}`, "Cache-Control": "private, no-store" };
  const fail = (status: number, error: string) => Response.json({ error }, { status, headers: base });
  return async (req: Request): Promise<Response | undefined> => {
    const { pathname } = new URL(req.url);
    if (!pathname.startsWith(PREFIX)) return undefined;
    const rest = pathname.slice(PREFIX.length);
    const allowed = (req.method === "POST" && rest === "resumes") ||
      (req.method === "GET" && (rest === "resumes" || new RegExp(`^resumes/${ID}(?:/time-savings|/tailored/${ID})?$`).test(rest)));
    if (!allowed) return fail(404, "not found");
    const cookie = sessionCookie(req);
    if (!cookie) return fail(401, "Open AviCV to start your private session.");
    const headers = new Headers({ Cookie: cookie, Origin: config.appOrigin });
    const init: RequestInit = { method: req.method, headers, redirect: "error", signal: AbortSignal.timeout(30_000) };
    if (req.method === "POST") {
      if (Number(req.headers.get("content-length") ?? 0) > MAX_UPLOAD_BYTES) return fail(413, "file too large");
      const body = await req.arrayBuffer();
      if (body.byteLength > MAX_UPLOAD_BYTES) return fail(413, "file too large");
      init.body = body;
      const type = req.headers.get("content-type");
      if (type) headers.set("Content-Type", type);
    }
    try {
      const res = await fetchFn(`${config.apiUrl}/${rest}`, init);
      return new Response(res.body, { status: res.status, headers: { ...base, "Content-Type": res.headers.get("content-type") ?? "application/json" } });
    } catch { return fail(502, "AviCV is unreachable"); }
  };
}
