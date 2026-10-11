import { expect, test } from "bun:test";
import { AGENT_INSTRUCTIONS, DARWIN_RULES } from "./instructions";
test("partner results are untrusted and agent discovery never becomes invented jobs", () => {
  for (const rule of DARWIN_RULES) expect(AGENT_INSTRUCTIONS).toContain(rule);
  expect(AGENT_INSTRUCTIONS).toContain("Never send a CV, contact information, or CV-derived details to Darwin");
});
test("job chat retains the factual boundary and simple product language", () => {
  expect(AGENT_INSTRUCTIONS).toContain("do not invent jobs, scores or experience");
  expect(AGENT_INSTRUCTIONS).toContain("Never show numerical scores or percentages");
  expect(AGENT_INSTRUCTIONS).toContain("backend time-savings estimate");
  expect(AGENT_INSTRUCTIONS).toContain("list_resumes");
});
