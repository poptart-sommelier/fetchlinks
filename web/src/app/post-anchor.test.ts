import { describe, expect, it } from "vitest";

import {
  postAnchorId,
  ratedPostIdFrom,
  withRatedAnchor,
} from "./post-anchor";

describe("withRatedAnchor", () => {
  it("adds the marker and the fragment to a bare path", () => {
    expect(withRatedAnchor("/", 12)).toBe("/?rated=12#post-12");
  });

  it("keeps the filters and page the reader was on", () => {
    expect(withRatedAnchor("/?q=AI&page=3", 12)).toBe(
      "/?q=AI&page=3&rated=12#post-12",
    );
  });

  it("replaces an existing marker rather than adding a second", () => {
    expect(withRatedAnchor("/?rated=9#post-9", 12)).toBe("/?rated=12#post-12");
  });

  it("never returns an absolute URL, whatever the base was", () => {
    expect(withRatedAnchor("/", 12).startsWith("/")).toBe(true);
  });
});

describe("ratedPostIdFrom", () => {
  it("reads a post id back off the URL", () => {
    expect(ratedPostIdFrom("12")).toBe(12);
  });

  it("takes the first of a repeated parameter", () => {
    expect(ratedPostIdFrom(["12", "13"])).toBe(12);
  });

  it.each([undefined, "", "abc", "-1", "0", "1.5", "9e99"])(
    "ignores %o rather than failing on it",
    (value) => {
      expect(ratedPostIdFrom(value as string | undefined)).toBeUndefined();
    },
  );
});

describe("postAnchorId", () => {
  it("is a valid fragment for any post id", () => {
    expect(postAnchorId(12)).toBe("post-12");
  });
});
