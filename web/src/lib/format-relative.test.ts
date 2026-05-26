import { describe, expect, it } from "vitest";

import { formatRelative } from "./format-relative";

const NOW = new Date("2026-05-26T12:00:00Z");

describe("formatRelative", () => {
  it("returns null for null/undefined", () => {
    expect(formatRelative(null, NOW)).toBeNull();
    expect(formatRelative(undefined, NOW)).toBeNull();
  });

  it("returns null for unparseable input", () => {
    expect(formatRelative("not a date", NOW)).toBeNull();
  });

  it("formats past times with 'ago'", () => {
    expect(formatRelative("2026-05-26T11:59:30Z", NOW)).toBe("30s ago");
    expect(formatRelative("2026-05-26T11:55:00Z", NOW)).toBe("5m ago");
    expect(formatRelative("2026-05-26T09:00:00Z", NOW)).toBe("3h ago");
    expect(formatRelative("2026-05-23T12:00:00Z", NOW)).toBe("3d ago");
    expect(formatRelative("2026-05-12T12:00:00Z", NOW)).toBe("2w ago");
    expect(formatRelative("2026-02-25T12:00:00Z", NOW)).toBe("3mo ago");
    expect(formatRelative("2024-05-26T12:00:00Z", NOW)).toBe("2y ago");
  });

  it("formats future times with 'in'", () => {
    expect(formatRelative("2026-05-26T12:05:00Z", NOW)).toBe("in 5m");
    expect(formatRelative("2027-05-26T12:00:00Z", NOW)).toBe("in 1y");
  });

  it("uses Date.now() by default", () => {
    expect(formatRelative(new Date().toISOString())).toMatch(/^\d+s ago$/);
  });
});
