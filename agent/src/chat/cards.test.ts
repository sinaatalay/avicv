import { describe, expect, test } from "bun:test";
import { bareToolName, jobCards, SUGGESTIONS, safeDownloadUrl, safeHttpsUrl, tailoredCard, tailorMessage, toolPayload } from "./cards";

const wrap = (payload: unknown, isError = false) => JSON.stringify({ content: [{ type: "text", text: JSON.stringify(payload) }], isError });

describe("toolPayload", () => {
  test("unwraps the CallToolResult text", () => {
    expect(toolPayload(wrap({ a: 1 }))).toEqual({ a: 1 });
  });
  test("null for errors, junk and missing results", () => {
    expect(toolPayload(wrap({ a: 1 }, true))).toBeNull();
    expect(toolPayload("not json")).toBeNull();
    expect(toolPayload(undefined)).toBeNull();
    expect(toolPayload(JSON.stringify({ content: [{ type: "text", text: "list_matches failed: 404 x" }] }))).toBeNull();
  });
});

describe("urls", () => {
  test("https only", () => {
    expect(safeHttpsUrl("https://a.example/x")).toBe("https://a.example/x");
    expect(safeHttpsUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpsUrl("http://a.example")).toBeNull();
    expect(safeHttpsUrl(5)).toBeNull();
  });
  test("download url must be on the app origin under /resumes/", () => {
    expect(safeDownloadUrl("https://app.example/resumes/a/tailored/b/cv.pdf", "https://app.example")).toBe("https://app.example/resumes/a/tailored/b/cv.pdf");
    expect(safeDownloadUrl("https://evil.example/resumes/a", "https://app.example")).toBeNull();
    expect(safeDownloadUrl("https://app.example/other", "https://app.example")).toBeNull();
  });
});

describe("jobCards", () => {
  test("maps list_matches items", () => {
    const cards = jobCards({
      items: [
        { job_key: "k1", title: "Backend", company: "Acme", locations: ["Remote", "NYC", "SF"], category: "good", salary_min: 100000, salary_max: 150000, salary_currency: "USD", salary_interval: "year", url: "https://acme.example/j" },
        { job_key: "k2", title: "Bad", company: "Evil", url: "javascript:x", score: 2 },
        { title: "no key", company: "x" },
      ],
    });
    expect(cards).toEqual([
      { jobKey: "k1", title: "Backend", company: "Acme", location: "Remote · NYC", category: "good", salary: "USD 100,000–150,000 / year", url: "https://acme.example/j" },
      { jobKey: "k2", title: "Bad", company: "Evil", location: null, category: "other", salary: null, url: null },
    ]);
  });
  test("jobCards keeps matches without a company", () => {
    const cards = jobCards({ items: [{ job_key: "k1", title: "Backend Engineer", company: null, score: 0.9 }, { job_key: "k2", title: "SRE", company: "Acme", score: 0.8 }] });
    expect(cards.map((c) => [c.jobKey, c.company])).toEqual([["k1", null], ["k2", "Acme"]]);
    expect(tailorMessage(cards[0]!)).toBe("Tailor my CV for Backend Engineer (job_key k1)");
  });
  test("jobCards ignores search_jobs rows", () => {
    expect(jobCards({ jobs: [{ id: "greenhouse:acme:123", title: "Backend Engineer", company: "Acme" }] })).toEqual([]);
    expect(jobCards({ items: [{ id: "greenhouse:acme:123", title: "Backend Engineer", company: "Acme" }] })).toEqual([]);
    expect(jobCards(null)).toEqual([]);
    expect(jobCards({ items: "x" })).toEqual([]);
  });
});

describe("tailoredCard", () => {
  test("ready card carries the download url, others do not", () => {
    const ready = { id: "t", status: "ready", error: null, download_url: "https://app.example/resumes/r/tailored/t/cv.pdf" };
    expect(tailoredCard(ready, "https://app.example")?.downloadUrl).toBe(ready.download_url);
    expect(tailoredCard({ ...ready, status: "tailoring" }, "https://app.example")?.downloadUrl).toBeNull();
    expect(tailoredCard({ status: "ready" }, "https://app.example")).toBeNull();
  });
});

test("tailorMessage and bareToolName", () => {
  expect(tailorMessage({ title: "Backend", company: "Acme", jobKey: "k1" })).toBe("Tailor my CV for Backend at Acme (job_key k1)");
  expect(bareToolName("cv__list_matches")).toBe("list_matches");
  expect(bareToolName("tailor_cv")).toBe("tailor_cv");
});

describe("SUGGESTIONS", () => {
  test("are the four spec 08 prompts and say AviCV, never amber or speedhack", () => {
    expect(SUGGESTIONS.map((s) => s.title)).toEqual([
      "Show my best matches",
      "Tailor my CV for my top match",
      "Find remote backend roles",
      "How much time has AviCV saved me?",
    ]);
    for (const s of SUGGESTIONS) expect(`${s.title} ${s.message}`).not.toMatch(/amber|speedhack/i);
  });
});

test("browser artifacts use durable authenticated download paths, not expiring MCP grants", () => {
  const resume = "a".repeat(32); const id = "b".repeat(32);
  expect(tailoredCard({ id, resume_id: resume, status: "ready", download_url: "https://app.example/downloads/expired-secret" }, "https://app.example")?.downloadUrl).toBe(`https://app.example/resumes/${resume}/tailored/${id}/cv.pdf`);
});
test("the backend category is authoritative; scores never become display percentages", () => {
  const [card] = jobCards({ items: [{ job_key: "j", title: "Engineer", score: 0.999, category: "other" }] });
  expect(card?.category).toBe("other"); expect(card).not.toHaveProperty("scorePct");
});
