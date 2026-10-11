import { describe, expect, test } from "bun:test";
import { isResumeId, readResumeId, rememberResumeId, uploadCv, uploadProblem } from "./resume";

const ID = "0123456789abcdef0123456789abcdef";
const pdf = (name = "cv.pdf") => new File(["%PDF-1.4"], name, { type: "application/pdf" });
const memory = (initial: Record<string, string> = {}) => {
  const data = { ...initial };
  return { getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => void (data[k] = v), data };
};

describe("readResumeId", () => {
  test("prefers a valid ?resume= over storage", () => {
    expect(readResumeId(`?resume=${ID}`, memory({ "avicv_resume_id:": "f".repeat(32) }))).toBe(ID);
  });
  test("ignores an invalid param and falls back to storage", () => {
    expect(readResumeId("?resume=../etc", memory({ "avicv_resume_id:": ID }))).toBe(ID);
  });
  test("null without either, or when storage throws", () => {
    expect(readResumeId("")).toBeNull();
    const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
    expect(readResumeId("", broken)).toBeNull();
    expect(() => rememberResumeId(ID, broken)).not.toThrow();
  });
  test("rememberResumeId then readResumeId round-trips", () => {
    const s = memory();
    rememberResumeId(ID, s);
    expect(readResumeId("", s)).toBe(ID);
    expect(isResumeId(ID.toUpperCase())).toBe(false);
  });
});

describe("uploadProblem", () => {
  test("accepts PDFs by type or extension, rejects others and oversize", () => {
    expect(uploadProblem({ name: "a.PDF", type: "", size: 10 })).toBeNull();
    expect(uploadProblem({ name: "a", type: "application/pdf", size: 10 })).toBeNull();
    expect(uploadProblem({ name: "a.docx", type: "", size: 10 })).toContain("PDF");
    expect(uploadProblem({ name: "a.pdf", type: "", size: 10 * 1024 * 1024 + 1 })).toContain("10 MB");
  });
});

describe("uploadCv", () => {
  test("posts multipart to the proxy and returns the id", async () => {
    let seen: { url: string; init?: RequestInit } | undefined;
    const fetchFn = (async (url: string, init?: RequestInit) => {
      seen = { url, init };
      return Response.json({ id: ID });
    }) as unknown as typeof fetch;
    expect(await uploadCv(pdf(), fetchFn)).toBe(ID);
    expect(seen?.url).toBe("/chat/avicv/resumes");
    expect(seen?.init?.method).toBe("POST");
    expect((seen?.init?.body as FormData).get("file")).toBeInstanceOf(File);
  });
  test("surfaces AviCV's detail and never fetches a non-PDF", async () => {
    const reject = (async () => Response.json({ detail: "Only PDF files are supported" }, { status: 415 })) as unknown as typeof fetch;
    await expect(uploadCv(pdf(), reject)).rejects.toThrow("Only PDF files are supported");
    let called = false;
    const spy = (async () => ((called = true), Response.json({}))) as unknown as typeof fetch;
    await expect(uploadCv(new File(["x"], "cv.txt"), spy)).rejects.toThrow("PDF");
    expect(called).toBe(false);
  });
  test("network failure and a missing id are clear errors", async () => {
    const down = (async () => { throw new Error("down"); }) as unknown as typeof fetch;
    await expect(uploadCv(pdf(), down)).rejects.toThrow("Could not reach AviCV");
    const bad = (async () => Response.json({ id: "nope" })) as unknown as typeof fetch;
    await expect(uploadCv(pdf(), bad)).rejects.toThrow("résumé id");
  });
});
