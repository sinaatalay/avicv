/** Pure parsing and formatting for the tool-result cards (spec 08). */

export interface JobCard {
  jobKey: string;
  title: string;
  company: string | null;
  location: string | null;
  category: "good" | "other";
  salary: string | null;
  url: string | null;
}

export interface TailoredCard {
  id: string;
  resumeId: string | null;
  status: string;
  error: string | null;
  downloadUrl: string | null;
}

const PREFIX = /^(?:avicv|cv)(?:__|_|\.|\/)/;

/** The bare cv-tools name, whatever prefix the runtime puts on MCP tools. */
export function bareToolName(name: string): string {
  return name.replace(PREFIX, "");
}

/**
 * The runtime sends `JSON.stringify(<MCP CallToolResult>)`; the tool's own JSON
 * is the first text part. Null for failures or anything unparseable.
 */
export function toolPayload(result: string | undefined): unknown {
  if (!result) return null;
  try {
    const outer: unknown = JSON.parse(result);
    if (!outer || typeof outer !== "object") return null;
    const { isError, content, structuredContent } = outer as { isError?: unknown; content?: unknown; structuredContent?: unknown };
    if (isError === true) return null;
    if (structuredContent && typeof structuredContent === "object") return structuredContent;
    if (!Array.isArray(content)) return outer;
    const text = (content as { type?: unknown; text?: unknown }[]).find((p) => p?.type === "text" && typeof p.text === "string")?.text;
    return typeof text === "string" ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

/** Only https links from outside text are ever rendered as hrefs. */
export function safeHttpsUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? value : null;
  } catch {
    return null;
  }
}

/** The download link must be on APP_ORIGIN (tools build it from there); anything else is dropped. */
export function safeDownloadUrl(value: unknown, appOrigin: string): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value, appOrigin);
    return !url.username && !url.password && url.origin === new URL(appOrigin).origin && url.pathname.startsWith("/resumes/") ? url.href : null;
  } catch {
    return null;
  }
}

const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function salaryLabel(min: number | null, max: number | null, currency: string | null, interval: string | null): string | null {
  if (min === null && max === null) return null;
  const fmt = (n: number) => Math.round(n).toLocaleString("en-US");
  const range = min !== null && max !== null && min !== max ? `${fmt(min)}–${fmt(max)}` : fmt((min ?? max)!);
  return [currency, range, interval ? `/ ${interval}` : null].filter(Boolean).join(" ");
}

/** Job cards out of a `list_matches` (`items`) payload; `search_jobs` ids are not match job_keys, so it gets none. */
export function jobCards(payload: unknown): JobCard[] {
  if (!payload || typeof payload !== "object") return [];
  const { items } = payload as { items?: unknown };
  const rows = Array.isArray(items) ? items : [];
  const cards: JobCard[] = [];
  for (const row of rows as Record<string, unknown>[]) {
    if (!row || typeof row !== "object") continue;
    const jobKey = str(row.job_key);
    const title = str(row.title);
    const company = str(row.company);
    if (!jobKey || !title) continue;
    const locations = Array.isArray(row.locations) ? row.locations.filter((l): l is string => typeof l === "string") : [];
    cards.push({
      jobKey,
      title,
      company,
      location: locations.length ? locations.slice(0, 2).join(" · ") : null,
      category: row.category === "good" ? "good" : "other",
      salary: salaryLabel(num(row.salary_min), num(row.salary_max), str(row.salary_currency), str(row.salary_interval)),
      url: safeHttpsUrl(row.url),
    });
  }
  return cards;
}

/** The card for `tailor_cv` / `get_tailored_cv`; null when the payload has no id and status. */
export function tailoredCard(payload: unknown, appOrigin: string): TailoredCard | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  const id = str(p.id);
  const status = str(p.status);
  if (!id || !status) return null;
  const resumeId = str(p.resume_id);
  const ownedPath = resumeId && /^[0-9a-f]{32}$/.test(resumeId) && /^[0-9a-f]{32}$/.test(id)
    ? `/resumes/${resumeId}/tailored/${id}/cv.pdf` : null;
  return { id, resumeId, status, error: str(p.error), downloadUrl: status === "ready" ? safeDownloadUrl(ownedPath ?? p.download_url, appOrigin) : null };
}

/** The chat message the "Tailor my CV" button sends. */
export function tailorMessage(card: Pick<JobCard, "title" | "company" | "jobKey">): string {
  return `Tailor my CV for ${card.title}${card.company ? ` at ${card.company}` : ""} (job_key ${card.jobKey})`;
}

/** Empty-state suggestion pills (spec 08 "Empty-state suggestions"). */
export const SUGGESTIONS: { title: string; message: string }[] = [
  { title: "Show my best matches", message: "Show my best matches" },
  { title: "Tailor my CV for my top match", message: "Tailor my CV for my top match" },
  { title: "Find remote backend roles", message: "Find remote backend roles" },
  { title: "How much time has AviCV saved me?", message: "How much time has AviCV saved me?" },
];
