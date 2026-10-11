import { describe, expect, test } from "bun:test";
import { catalogLabel, isActiveStatus, resumeChip, resumeView, timeSavedLabel } from "./strip";

describe("header strip helpers", () => {
  test("polls only while the résumé is being processed", () => {
    for (const s of ["queued", "parsing", "matching", "waiting_for_jobs"]) expect(isActiveStatus(s)).toBe(true);
    for (const s of ["done", "failed", "", null, undefined]) expect(isActiveStatus(s)).toBe(false);
  });

  test("catalogLabel formats the open job count", () => {
    expect(catalogLabel({ open_jobs: 48210 })).toBe("48,210 open jobs");
    expect(catalogLabel({ open_jobs: 1 })).toBe("1 open job");
    expect(catalogLabel({ open_jobs: "x" })).toBeNull();
    expect(catalogLabel(null)).toBeNull();
  });

  test("resumeView and resumeChip name the CV and show matching progress", () => {
    const view = resumeView({ status: "matching", filename: "cv.pdf", cv: { name: "Ada Example" }, progress: { done: 12, total: 40 } })!;
    expect(resumeChip(view)).toBe("Ada Example · Finding your matches");
    const early = resumeView({ status: "waiting_for_jobs", filename: "cv.pdf", cv: null, progress: null })!;
    expect(resumeChip(early)).toBe("cv.pdf · Finding your matches");
    expect(resumeChip(resumeView({ status: "done", cv: { name: "Ada" }, progress: { done: 40, total: 40 } })!)).toBe("Ada · Matches ready");
    expect(resumeChip(resumeView({ status: "parsing" })!)).toBe("Your CV · Reading your CV");
    expect(resumeView({ detail: "nope" })).toBeNull();
  });

  test("timeSavedLabel rounds to hours and minutes", () => {
    expect(timeSavedLabel({ estimated_seconds: 4800 })).toBe("≈ 1 h 20 min saved");
    expect(timeSavedLabel({ estimated_seconds: 7200 })).toBe("≈ 2 h saved");
    expect(timeSavedLabel({ estimated_seconds: 600 })).toBe("≈ 10 min saved");
    expect(timeSavedLabel({ estimated_seconds: 20 })).toBeNull();
    expect(timeSavedLabel({})).toBeNull();
  });
});
