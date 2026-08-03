import { describe, expect, it } from "vitest";

import { escapeLikeValue, SqlParams, utcIso } from "./sql";

describe("SqlParams", () => {
  it("numbers placeholders in the order values are added", () => {
    const params = new SqlParams();

    expect(params.next("a")).toBe("$1");
    expect(params.next(2)).toBe("$2");
    expect(params.next(null)).toBe("$3");
    expect(params.toArray()).toEqual(["a", 2, null]);
  });

  it("reuses one placeholder when the same value is referenced repeatedly", () => {
    const params = new SqlParams();
    const pattern = params.next("%term%");

    expect(`a LIKE ${pattern} OR b LIKE ${pattern}`).toBe(
      "a LIKE $1 OR b LIKE $1",
    );
    expect(params.toArray()).toEqual(["%term%"]);
  });

  it("hands back a copy so callers cannot mutate the collected values", () => {
    const params = new SqlParams();
    params.next("a");

    params.toArray().push("b");

    expect(params.toArray()).toEqual(["a"]);
  });
});

describe("escapeLikeValue", () => {
  it("neutralises LIKE wildcards so a search matches them literally", () => {
    expect(escapeLikeValue("100%")).toBe("100\\%");
    expect(escapeLikeValue("a_b")).toBe("a\\_b");
  });

  it("escapes the escape character first", () => {
    expect(escapeLikeValue("a\\b")).toBe("a\\\\b");
    expect(escapeLikeValue("\\%")).toBe("\\\\\\%");
  });

  it("leaves ordinary text alone", () => {
    expect(escapeLikeValue("fetchlinks")).toBe("fetchlinks");
  });
});

describe("utcIso", () => {
  it("renders the expression as ISO-8601 in UTC", () => {
    expect(utcIso("posts.posted_at")).toBe(
      `to_char(posts.posted_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')`,
    );
  });
});
