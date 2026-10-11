const RESUME_ID = /^[0-9a-f]{32}$/;
const STORAGE_KEY = "avicv_resume_id";
/** AviCV's upload limit. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

export function isResumeId(value: unknown): value is string {
  return typeof value === "string" && RESUME_ID.test(value);
}

/** The résumé id from `?resume=`, else the remembered one, else null. Storage may throw (embedded, private mode). */
export function readResumeId(search: string, storage?: Storage, workspaceId = ""): string | null {
  const fromUrl = new URLSearchParams(search).get("resume");
  if (isResumeId(fromUrl)) return fromUrl;
  try {
    const stored = storage?.getItem(`${STORAGE_KEY}:${workspaceId}`);
    return isResumeId(stored) ? stored : null;
  } catch {
    return null;
  }
}

export function rememberResumeId(id: string, storage?: Storage, workspaceId = ""): void {
  try {
    storage?.setItem(`${STORAGE_KEY}:${workspaceId}`, id);
  } catch {
    // storage unavailable: the id still lives in the URL
  }
}

/** Why a file cannot go to AviCV, or null when it can. */
export function uploadProblem(file: { name: string; type: string; size: number }): string | null {
  const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  if (!isPdf) return "AviCV reads PDF CVs only. Please choose a PDF.";
  if (file.size > MAX_UPLOAD_BYTES) return "That PDF is over AviCV's 10 MB limit.";
  return null;
}

/** Posts a PDF to the same-origin proxy and returns the new résumé id; throws an Error with a user-facing message. */
export async function uploadCv(file: File, fetchFn: typeof fetch = fetch): Promise<string> {
  const problem = uploadProblem(file);
  if (problem) throw new Error(problem);
  const form = new FormData();
  form.append("file", file);
  let res: Response;
  try {
    res = await fetchFn("/chat/avicv/resumes", { method: "POST", body: form });
  } catch {
    throw new Error("Could not reach AviCV. Try again in a moment.");
  }
  const body = (await res.json().catch(() => ({}))) as { id?: unknown; detail?: unknown; error?: unknown };
  if (!res.ok) {
    const reason = typeof body.detail === "string" ? body.detail : typeof body.error === "string" ? body.error : `status ${res.status}`;
    throw new Error(`AviCV rejected the upload: ${reason}`);
  }
  if (!isResumeId(body.id)) throw new Error("AviCV did not return a résumé id.");
  return body.id;
}
