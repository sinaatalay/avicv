/** Pure formatting and polling rules for the AviCV header strip (spec 08). */

export const POLL_MS = 15_000;
const ACTIVE = new Set(["queued", "parsing", "matching", "waiting_for_jobs"]);

export interface ResumeView {
  status: string;
  filename: string | null;
  cvName: string | null;
  done: number;
  total: number;
}

/** The strip polls every POLL_MS only while the résumé is still being processed. */
export function isActiveStatus(status: string | null | undefined): boolean {
  return status != null && ACTIVE.has(status);
}

/** "48,210 open jobs", or null when the stats are unusable. */
export function catalogLabel(stats: unknown): string | null {
  const open = (stats as { open_jobs?: unknown } | null)?.open_jobs;
  if (typeof open !== "number" || !Number.isFinite(open) || open < 0) return null;
  const n = Math.floor(open);
  return `${n.toLocaleString("en-US")} open ${n === 1 ? "job" : "jobs"}`;
}

/** Reads the fields the strip needs out of `GET /resumes/:id`; null when it is not a résumé. */
export function resumeView(body: unknown): ResumeView | null {
  if (typeof body !== "object" || body === null) return null;
  const r = body as { status?: unknown; filename?: unknown; cv?: { name?: unknown } | null; progress?: { done?: unknown; total?: unknown } | null };
  if (typeof r.status !== "string") return null;
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
  return {
    status: r.status,
    filename: typeof r.filename === "string" && r.filename ? r.filename : null,
    cvName: typeof r.cv?.name === "string" && r.cv.name ? r.cv.name : null,
    done: num(r.progress?.done),
    total: num(r.progress?.total),
  };
}

/** "Ada Example · matching 12/40", or "Ada Example · done". */
export function resumeChip(view: ResumeView): string {
  const name = view.cvName ?? view.filename ?? "Your CV";
  const labels: Record<string, string> = { queued: "Preparing your CV", parsing: "Reading your CV", matching: "Finding your matches", waiting_for_jobs: "Finding your matches", done: "Matches ready", failed: "Needs another try" };
  return `${name} · ${labels[view.status] ?? "Preparing your CV"}`;
}

/** "≈ 1 h 20 min saved" from time-savings `estimated_seconds`; null when nothing is saved yet. */
export function timeSavedLabel(body: unknown): string | null {
  const seconds = (body as { estimated_seconds?: unknown } | null)?.estimated_seconds;
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 60) return null;
  const minutes = Math.round(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  const text = h === 0 ? `${m} min` : m === 0 ? `${h} h` : `${h} h ${m} min`;
  return `≈ ${text} saved`;
}
