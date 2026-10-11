import type { Config } from "./config";

export interface Session { workspaceId: string; cookie: string }
export type Fetch = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

/** Only the AviCV credential crosses the trusted backend boundary. */
export function sessionCookie(req: Request): string | null {
  const values = (req.headers.get("cookie") ?? "").split(";").map((s) => s.trim()).filter((s) => s.startsWith("avicv_session="));
  if (values.length !== 1) return null;
  const value = values[0]!.slice("avicv_session=".length);
  return /^[A-Za-z0-9_-]{20,256}$/.test(value) ? `avicv_session=${value}` : null;
}

export async function authenticate(req: Request, config: Config, fetchFn: Fetch = fetch): Promise<Session | Response> {
  const cookie = sessionCookie(req);
  if (!cookie) return Response.json({ error: "Open AviCV to start your private session." }, { status: 401 });
  try {
    const response = await fetchFn(`${config.apiUrl}/auth/session`, {
      headers: { Cookie: cookie }, redirect: "error", signal: AbortSignal.timeout(5000),
    });
    if (response.status === 401 || response.status === 403) return Response.json({ error: "Your session has expired. Open AviCV again." }, { status: 401 });
    if (!response.ok) throw new Error("Session validation unavailable");
    const data = await response.json() as { workspace_id?: unknown };
    if (typeof data.workspace_id !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(data.workspace_id)) throw new Error("Invalid owner");
    return { workspaceId: data.workspace_id, cookie };
  } catch {
    return Response.json({ error: "AviCV is temporarily unavailable." }, { status: 503 });
  }
}

/** Browser mutation endpoints require the deployed same-origin app. */
export function hasTrustedOrigin(req: Request, config: Config): boolean {
  const origin = req.headers.get("origin");
  return origin === config.appOrigin || (!origin && req.headers.get("sec-fetch-site") === "same-origin");
}
