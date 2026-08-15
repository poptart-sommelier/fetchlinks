import { describe, expect, it } from "vitest";

import {
  formatAge,
  formatByteChange,
  formatBytes,
  formatDuration,
  MISSING,
} from "./format-facts";

describe("formatDuration", () => {
  it("keeps milliseconds for the very short", () => {
    expect(formatDuration(0)).toBe("0ms");
    expect(formatDuration(940)).toBe("940ms");
  });

  it("switches to seconds, then minutes, then hours", () => {
    expect(formatDuration(1500)).toBe("1.5s");
    expect(formatDuration(42_000)).toBe("42s");
    expect(formatDuration(90_000)).toBe("1m 30s");
    expect(formatDuration(120_000)).toBe("2m");
    expect(formatDuration(3_930_000)).toBe("1h 5m");
  });

  it("admits when it does not know", () => {
    expect(formatDuration(null)).toBe(MISSING);
    expect(formatDuration(undefined)).toBe(MISSING);
    expect(formatDuration(-1)).toBe(MISSING);
  });
});

describe("formatAge", () => {
  it("reads seconds as a duration", () => {
    expect(formatAge(90)).toBe("1m 30s");
  });

  it("does not invent an age it does not have", () => {
    expect(formatAge(null)).toBe(MISSING);
  });
});

describe("formatBytes", () => {
  it("scales at 1024", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5 MB");
    expect(formatBytes(1.5 * 1024 * 1024 * 1024)).toBe("1.5 GB");
  });

  it("never prints zero for an unknown figure", () => {
    // A status page that says "0 B free" when it simply has no measurement is
    // worse than one that says nothing.
    expect(formatBytes(null)).toBe(MISSING);
    expect(formatBytes(Number.NaN)).toBe(MISSING);
  });
});

describe("formatByteChange", () => {
  it("signs a change so growth reads as growth", () => {
    expect(formatByteChange(4 * 1024 * 1024)).toBe("+4 MB");
    expect(formatByteChange(-2048)).toBe("-2 KB");
  });

  it("distinguishes no change from nothing to compare with", () => {
    expect(formatByteChange(0)).toBe("no change");
    expect(formatByteChange(null)).toBe(MISSING);
  });
});
