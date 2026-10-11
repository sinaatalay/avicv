import type { Config } from "./config";
import type { Fetch, Session } from "./auth";

const active = new Map<string, number>();
let activeTotal = 0;
export interface ChatLease { started: boolean; release(): void }
/** Deployment runs one chat process; daily limits are durable in FastAPI. */
export function reserveChat(workspaceId: string): ChatLease | null {
  if ((active.get(workspaceId) ?? 0) >= 1 || activeTotal >= 4) return null;
  active.set(workspaceId, (active.get(workspaceId) ?? 0) + 1); activeTotal++;
  let released = false;
  return { started: false, release() {
    if (released) return; released = true;
    const count = (active.get(workspaceId) ?? 1) - 1;
    if (count) active.set(workspaceId, count); else active.delete(workspaceId);
    activeTotal--;
  } };
}
export async function admitChat(config: Config, session: Session, fetchFn: Fetch = fetch): Promise<Response | null> {
  try {
    const response = await fetchFn(`${config.apiUrl}/auth/chat-admission`, { method: "POST", headers: { Cookie: session.cookie, Origin: config.appOrigin, "Content-Type": "application/json" }, body: "{}", redirect: "error", signal: AbortSignal.timeout(5000) });
    if (response.ok) return null;
    const limited = response.status === 429;
    return Response.json({ error: limited ? "You've reached the chat limit for now. Please try again later." : "Chat is temporarily unavailable. Please try again." }, { status: limited ? 429 : 503, headers: { "Retry-After": response.headers.get("Retry-After") || "30" } });
  } catch { return Response.json({ error: "Chat is temporarily unavailable. Please try again." }, { status: 503, headers: { "Retry-After": "30" } }); }
}
