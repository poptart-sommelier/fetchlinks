import { describe, expect, it } from "vitest";

import { safeExternalHref } from "./safe-external-href";

describe("safeExternalHref", () => {
  it("returns http(s) URLs unchanged (modulo URL parsing)", () => {
    expect(safeExternalHref("https://example.com/foo?bar=1#x")).toBe(
      "https://example.com/foo?bar=1#x",
    );
    expect(safeExternalHref("http://example.com")).toBe("http://example.com/");
  });

  it("upgrades bare hostnames to https", () => {
    expect(safeExternalHref("example.com")).toBe("https://example.com/");
    expect(safeExternalHref("  reddit.com/r/foo  ")).toBe(
      "https://reddit.com/r/foo",
    );
  });

  it("rejects javascript: URLs", () => {
    expect(safeExternalHref("javascript:alert(1)")).toBeNull();
    expect(safeExternalHref("JavaScript:alert(1)")).toBeNull();
    expect(safeExternalHref(" javascript:alert(1) ")).toBeNull();
  });

  it("rejects other dangerous schemes", () => {
    expect(safeExternalHref("data:text/html,<script>alert(1)</script>")).toBeNull();
    expect(safeExternalHref("vbscript:msgbox(1)")).toBeNull();
    expect(safeExternalHref("file:///etc/passwd")).toBeNull();
    expect(safeExternalHref("chrome://settings")).toBeNull();
  });

  it("rejects empty / nullish input", () => {
    expect(safeExternalHref(null)).toBeNull();
    expect(safeExternalHref(undefined)).toBeNull();
    expect(safeExternalHref("")).toBeNull();
    expect(safeExternalHref("   ")).toBeNull();
  });

  it("rejects scheme-less strings that contain a colon", () => {
    expect(safeExternalHref("foo:bar")).toBeNull();
  });
});
