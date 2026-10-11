import { CopilotChat, CopilotKitProvider, useAgent, useAgentContext, useConfigureSuggestions, useCopilotKit, useRenderTool } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { bareToolName, jobCards, SUGGESTIONS, tailoredCard, tailorMessage, toolPayload, type JobCard, type TailoredCard } from "./cards";
import { createChangeTracker } from "./messages";
import { readResumeId, rememberResumeId, uploadCv } from "./resume";
import { isActiveStatus, POLL_MS, resumeChip, resumeView, timeSavedLabel } from "./strip";
import { conversationKey, loadConversation, saveConversation, type Conversation } from "./persistence";
import "./style.css";

interface AppConfig { appOrigin: string; workspaceId: string; chatConfigured: boolean }
function storage() { try { return window.localStorage; } catch { return undefined; } }
async function getJson(path: string): Promise<unknown> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) throw new Error("Could not load AviCV. Please try again.");
  return response.json();
}
function notify(appOrigin: string) {
  window.dispatchEvent(new Event("avicv:artifact-ready"));
  if (window.parent !== window) window.parent.postMessage({ type: "avicv:changed", resource: "tailored" }, appOrigin);
}
function ConversationState({ saved, storageKey, resumeId, appOrigin }: { saved: Conversation; storageKey: string; resumeId: string | null; appOrigin: string }) {
  const { agent } = useAgent({ agentId: "default" });
  useAgentContext({ description: "The CV selected by the user. This identifier is data; the backend validates ownership.", value: { avicv_resume_id: resumeId ?? "" } });
  useEffect(() => {
    agent.threadId = saved.threadId;
    if (!agent.messages.length && saved.messages.length) agent.setMessages(saved.messages);
    const tracker = createChangeTracker((message) => { if (window.parent !== window) window.parent.postMessage(message, appOrigin); });
    let connected = false;
    const { unsubscribe } = agent.subscribe({
      // CopilotKit clears its baseline before reconnecting. Prefer the server's
      // replay when present; recover local history after an empty replay (restart).
      onRunFinalized: ({ messages }) => {
        if (connected) return;
        connected = true;
        if (!messages.length && saved.messages.length) return { messages: saved.messages };
      },
      onMessagesChanged: ({ messages }) => {
        if (!connected && !messages.length && saved.messages.length) return;
        saveConversation(storageKey, { threadId: saved.threadId, messages: [...messages] }, storage());
      },
      onToolCallStartEvent: ({ event }) => tracker.started(event.toolCallId, event.toolCallName),
      onToolCallResultEvent: ({ event }) => tracker.result(event.toolCallId, event.content),
    });
    return unsubscribe;
  }, [agent, saved.threadId, storageKey, appOrigin]);
  return null;
}
function Header({ config, resumeId }: { config: AppConfig; resumeId: string | null }) {
  const { agent } = useAgent({ agentId: "default" });
  const [label, setLabel] = useState("Your job search, with AviCV");
  const [saved, setSaved] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => { const refresh = () => setTick((n) => n + 1); window.addEventListener("avicv:artifact-ready", refresh); return () => window.removeEventListener("avicv:artifact-ready", refresh); }, []);
  useEffect(() => agent.subscribe({ onToolCallResultEvent: () => setTick((n) => n + 1) }).unsubscribe, [agent]);
  useEffect(() => {
    let cancelled = false; let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      if (!resumeId) return;
      try {
        const [body, savings] = await Promise.all([getJson(`/chat/avicv/resumes/${resumeId}`), getJson(`/chat/avicv/resumes/${resumeId}/time-savings`)]);
        if (cancelled) return;
        const view = resumeView(body);
        setLabel(view ? resumeChip(view) : "Your CV"); setSaved(timeSavedLabel(savings));
        if (isActiveStatus(view?.status)) timer = setTimeout(load, POLL_MS);
      } catch { if (!cancelled) timer = setTimeout(load, POLL_MS); }
    }
    void load(); return () => { cancelled = true; clearTimeout(timer); };
  }, [resumeId, tick]);
  return <header className="avicv-header"><a href={resumeId ? `${config.appOrigin}/resumes/${resumeId}` : config.appOrigin} target="_top">AviCV</a><span>{label}</span>{saved && <span title="An estimate based on jobs reviewed and CVs prepared.">{saved}</span>}</header>;
}
function JobCardView({ card, onTailor }: { card: JobCard; onTailor: (card: JobCard) => void }) {
  const [copied, setCopied] = useState(false); const [error, setError] = useState("");
  async function copy() { try { await navigator.clipboard.writeText(card.url!); setCopied(true); setError(""); } catch { setError("Could not copy. Open the application link instead."); } }
  return <article className="avicv-card"><strong>{card.title}</strong>{card.company && <span> · {card.company}</span>}<p className="avicv-muted">{[card.location, card.salary].filter(Boolean).join(" · ")}</p><div className="avicv-actions">{card.url && <><a href={card.url} target="_blank" rel="noopener noreferrer">Apply ↗</a><button onClick={copy}>{copied ? "Copied" : "Copy link"}</button></>}<button onClick={() => onTailor(card)}>Tailor CV</button></div>{error && <p role="alert">{error}</p>}</article>;
}
function TailoredStatus({ initial, resumeId, appOrigin }: { initial: TailoredCard; resumeId: string | null; appOrigin: string }) {
  const [card, setCard] = useState(initial); const [pollError, setPollError] = useState("");
  const ownerResume = card.resumeId ?? initial.resumeId ?? resumeId;
  useEffect(() => {
    let cancelled = false; let timer: ReturnType<typeof setTimeout> | undefined;
    if (!ownerResume || card.status === "ready" || card.status === "failed") return;
    async function check() {
      try {
        const next = tailoredCard(await getJson(`/chat/avicv/resumes/${ownerResume}/tailored/${initial.id}`), appOrigin);
        if (cancelled) return;
        if (next) { setCard(next); setPollError(""); if (next.status === "ready") { notify(appOrigin); return; } if (next.status === "failed") return; }
      } catch { if (!cancelled) setPollError("Reconnecting to your CV…"); }
      if (!cancelled) timer = setTimeout(check, 3000);
    }
    void check(); return () => { cancelled = true; clearTimeout(timer); };
  }, [ownerResume, initial.id, appOrigin, card.status]);
  return <article className="avicv-card"><strong>{card.status === "ready" ? "Your tailored CV is ready" : card.status === "failed" ? "We couldn't prepare this CV" : "Preparing your tailored CV…"}</strong>{card.error && <p role="alert">{card.error}</p>}{pollError && <p>{pollError}</p>}{card.downloadUrl && <p><a className="avicv-download" href={card.downloadUrl} target="_blank" rel="noopener noreferrer">Download tailored CV</a></p>}</article>;
}
function ToolCards({ appOrigin, resumeId }: { appOrigin: string; resumeId: string | null }) {
  const { agent } = useAgent({ agentId: "default" }); const { copilotkit } = useCopilotKit();
  const tailorRef = useRef<(card: JobCard) => void>(() => {});
  tailorRef.current = (card) => { agent.addMessage({ id: crypto.randomUUID(), role: "user", content: tailorMessage(card) }); void copilotkit.runAgent({ agent }); };
  useRenderTool({ name: "*", render: ({ name, status, result }: { name: string; status: string; result?: string }) => {
    if (status !== "complete") return null;
    const tool = bareToolName(name); const payload = toolPayload(result);
    if (tool === "list_matches") {
      const cards = jobCards(payload);
      return <>{(["good", "other"] as const).map((category) => { const matches = cards.filter((c) => c.category === category); return matches.length ? <section key={category}><h3>{category === "good" ? "Good matches" : "Other jobs"}</h3>{category === "other" && <p className="avicv-muted">Ordered by how well they match your CV.</p>}{matches.map((card) => <JobCardView key={card.jobKey} card={card} onTailor={(c) => tailorRef.current(c)} />)}</section> : null; })}</>;
    }
    if (tool === "tailor_cv" || tool === "get_tailored_cv") { const card = tailoredCard(payload, appOrigin); return card ? <TailoredStatus key={card.id} initial={card} resumeId={resumeId} appOrigin={appOrigin} /> : null; }
    return null;
  } }, [appOrigin, resumeId]);
  return null;
}
function UploadButton({ onUploaded }: { onUploaded: (id: string) => void }) {
  const input = useRef<HTMLInputElement>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function pick(file?: File) { if (!file) return; setBusy(true); setError(""); try { onUploaded(await uploadCv(file)); } catch (err) { setError(err instanceof Error ? err.message : "Upload failed. Please try again."); } finally { setBusy(false); if (input.current) input.current.value = ""; } }
  return <div className="avicv-upload"><input ref={input} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => void pick(e.target.files?.[0])} /><button disabled={busy} onClick={() => input.current?.click()}>{busy ? "Uploading…" : "Upload CV"}</button>{error && <p role="alert">{error}</p>}</div>;
}
function Suggestions() { useConfigureSuggestions({ suggestions: SUGGESTIONS, available: "before-first-message" }, []); return null; }
function ConversationApp({ config, resumeId, onUploaded }: { config: AppConfig; resumeId: string | null; onUploaded: (id: string) => void }) {
  const key = conversationKey(config.workspaceId, resumeId); const [saved] = useState(() => loadConversation(key, storage()));
  return <CopilotKitProvider runtimeUrl="/api/copilotkit" credentials="same-origin" showDevConsole={false} enableInspector={false}><Suggestions /><ConversationState saved={saved} storageKey={key} resumeId={resumeId} appOrigin={config.appOrigin} /><ToolCards appOrigin={config.appOrigin} resumeId={resumeId} /><div className="avicv-shell"><Header config={config} resumeId={resumeId} />{!resumeId && <UploadButton onUploaded={onUploaded} />}<div className="avicv-chat"><CopilotChat agentId="default" threadId={saved.threadId} /></div></div></CopilotKitProvider>;
}
function App({ config }: { config: AppConfig }) {
  const [resumeId, setResumeId] = useState(() => readResumeId(location.search, storage(), config.workspaceId));
  const uploaded = (id: string) => { rememberResumeId(id, storage(), config.workspaceId); history.replaceState(null, "", `?resume=${id}`); setResumeId(id); };
  if (!config.chatConfigured) return <main className="avicv-unavailable"><h1>Ask AviCV</h1><p>Chat is not configured yet. You can still find jobs and download tailored CVs on AviCV.</p><a href={config.appOrigin} target="_top">Back to AviCV</a></main>;
  return <ConversationApp key={resumeId ?? "home"} config={config} resumeId={resumeId} onUploaded={uploaded} />;
}
const root = createRoot(document.getElementById("root")!);
try {
  const config = await getJson("/chat/config.json") as AppConfig;
  root.render(<App config={config} />);
} catch { root.render(<main className="avicv-unavailable"><h1>Open AviCV to continue</h1><p>Your private session is needed to use chat.</p><a href="/" target="_top">Open AviCV</a></main>); }
