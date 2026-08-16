import { describe, expect, it } from "vitest";

import {
  managedPostIdFrom,
  postAnchorId,
  withManagedAnchor,
} from "./manage-anchor";

describe("withManagedAnchor", () => {
  it("adds the marker and fragment to a bare path", () => {
    expect(withManagedAnchor("/", 12)).toBe("/?managed=12#post-12");
  });

  it("keeps the filters and page the reader was on", () => {
    expect(withManagedAnchor("/?q=AI&page=3", 12)).toBe(
      "/?q=AI&page=3&managed=12#post-12",
    );
  });

  it("replaces an existing marker rather than adding a second", () => {
    expect(withManagedAnchor("/?managed=9#post-9", 12)).toBe(
      "/?managed=12#post-12",
    );
  });

  it("never returns an absolute URL", () => {
    expect(withManagedAnchor("/", 12).startsWith("/")).toBe(true);
  });
});

describe("managedPostIdFrom", () => {
  it("reads a post id back off the URL", () => {
    expect(managedPostIdFrom("12")).toBe(12);
  });

  it("takes the first of a repeated parameter", () => {
    expect(managedPostIdFrom(["12", "13"])).toBe(12);
  });

  it.each([undefined, "", "abc", "-1", "0", "1.5", "9e99"])(
    "ignores %o rather than failing on it",
    (value) => {
      expect(managedPostIdFrom(value as string | undefined)).toBeUndefined();
    },
  );
});

describe("postAnchorId", () => {
  it("is a valid fragment for any post id", () => {
    expect(postAnchorId(12)).toBe("post-12");
  });
});
