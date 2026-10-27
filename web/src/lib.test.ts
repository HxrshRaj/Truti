import { describe, expect, it } from "vitest";
import { parseRoute, sparkPoints, timeAgo } from "./lib";

describe("lib", () => {
  it("sparkPoints scales to the max and handles empty/zero data", () => {
    expect(sparkPoints([])).toBe("");
    const pts = sparkPoints([0, 5, 10], 100, 24).split(" ");
    expect(pts).toHaveLength(3);
    expect(pts[0].startsWith("0.0,")).toBe(true);
    expect(pts[2]).toBe("100.0,2.0"); // max value sits at the top
    expect(sparkPoints([0, 0], 10, 24)).toBe("0.0,22.0 10.0,22.0");
  });
  it("timeAgo", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(timeAgo("2026-01-01T11:59:30Z", now)).toBe("30s ago");
    expect(timeAgo("2026-01-01T11:00:00Z", now)).toBe("1h ago");
    expect(timeAgo("2025-12-30T12:00:00Z", now)).toBe("2d ago");
    expect(timeAgo(null)).toBe("—");
  });
  it("parseRoute", () => {
    expect(parseRoute("#/projects/3/issues/12")).toEqual({ project: 3, tab: "issues", issue: 12 });
    expect(parseRoute("#/projects/3/alerts")).toEqual({ project: 3, tab: "alerts", issue: undefined });
    expect(parseRoute("")).toEqual({ tab: "issues" });
  });
});
