import { describe, expect, test } from "bun:test";
import { changedMessage, createChangeTracker, resourceForTool } from "./messages";

test("maps tailoring tools to tailored and rematch to resume", () => {
  expect(resourceForTool("tailor_cv")).toBe("tailored");
  expect(resourceForTool("get_tailored_cv")).toBe("tailored");
  expect(resourceForTool("rematch")).toBe("resume");
  expect(resourceForTool("cv_rematch")).toBe("resume");
});

test("strips one MCP prefix", () => {
  for (const p of ["cv_", "cv__", "cv.", "cv/"]) {
    expect(resourceForTool(`${p}tailor_cv`)).toBe("tailored");
  }
  expect(resourceForTool("cv_cv_tailor_cv")).toBeNull();
});

test("other tools and inherited keys give null", () => {
  expect(resourceForTool("get_resume")).toBeNull();
  expect(resourceForTool("search")).toBeNull();
  expect(resourceForTool("toString")).toBeNull();
  expect(resourceForTool("")).toBeNull();
});

test("changedMessage shape", () => {
  expect(changedMessage("tailored")).toEqual({ type: "avicv:changed", resource: "tailored" });
});

const ok = JSON.stringify({ content: [{ type: "text", text: '{"id":"j1"}' }] });
const fail = JSON.stringify({ content: [{ type: "text", text: "get_tailored_cv failed: 422" }], isError: true });

describe("createChangeTracker", () => {
  const setup = () => {
    const posted: unknown[] = [];
    return { posted, tracker: createChangeTracker((m) => posted.push(m)) };
  };

  test("posts once per successful cv-tools call", () => {
    const { posted, tracker } = setup();
    tracker.started("t1", "cv_tailor_cv");
    tracker.result("t1", ok);
    tracker.result("t1", ok);
    expect(posted).toEqual([{ type: "avicv:changed", resource: "tailored" }]);
  });

  test("ignores failures, read tools and unknown calls", () => {
    const { posted, tracker } = setup();
    tracker.started("t1", "get_tailored_cv");
    tracker.result("t1", fail);
    tracker.started("t2", "get_resume");
    tracker.result("t2", ok);
    tracker.result("t3", ok);
    expect(posted).toEqual([]);
  });

  test("falls back to the failed-text prefix when content is not JSON", () => {
    const { posted, tracker } = setup();
    tracker.started("t1", "tailor_cv");
    tracker.result("t1", "tailor_cv failed: 500");
    expect(posted).toEqual([]);
  });
});
