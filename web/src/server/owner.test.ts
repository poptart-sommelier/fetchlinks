import { describe, expect, it } from "vitest";

import {
  createOwnerToken,
  isValidOwnerToken,
  OWNER_SESSION_MAX_AGE_SECONDS,
  safeReturnPath,
} from "./owner";

const ENV = {
  FETCHLINKS_ADMIN_USER: "rich",
  FETCHLINKS_ADMIN_PASS: "correct horse",
};

const NOW = Date.UTC(2026, 7, 11, 12, 0, 0);

describe("owner sessions", () => {
  it("issues a token that it accepts back", async () => {
    const token = await createOwnerToken(ENV, NOW);

    expect(token).toBeTypeOf("string");
    await expect(isValidOwnerToken(token, ENV, NOW)).resolves.toBe(true);
  });

  it("rejects a token once its own expiry has passed", async () => {
    const token = await createOwnerToken(ENV, NOW);
    const oneSecondLate = NOW + (OWNER_SESSION_MAX_AGE_SECONDS + 1) * 1000;

    await expect(isValidOwnerToken(token, ENV, oneSecondLate)).resolves.toBe(
      false,
    );
  });

  it("rejects a token whose expiry has been extended by hand", async () => {
    const token = (await createOwnerToken(ENV, NOW)) ?? "";
    const [version, , signature] = token.split(".");
    const forged = `${version}.99999999999.${signature}`;

    await expect(isValidOwnerToken(forged, ENV, NOW)).resolves.toBe(false);
  });

  it("stops accepting old tokens once the admin password changes", async () => {
    const token = await createOwnerToken(ENV, NOW);
    const rotated = { ...ENV, FETCHLINKS_ADMIN_PASS: "something else" };

    await expect(isValidOwnerToken(token, rotated, NOW)).resolves.toBe(false);
  });

  it("issues nothing when the admin credentials are unset", async () => {
    await expect(createOwnerToken({}, NOW)).resolves.toBeUndefined();
    await expect(isValidOwnerToken("anything", {}, NOW)).resolves.toBe(false);
  });

  it("treats malformed and empty tokens as anonymous", async () => {
    for (const value of [undefined, "", "nonsense", "v1.123", "v2.123.abc"]) {
      await expect(isValidOwnerToken(value, ENV, NOW)).resolves.toBe(false);
    }
  });

  it("keeps a local return path but discards anything off-site", () => {
    expect(safeReturnPath("/?source_type=reddit&page=3")).toBe(
      "/?source_type=reddit&page=3",
    );
    expect(safeReturnPath("//evil.example.com")).toBe("/");
    expect(safeReturnPath("/\\evil.example.com")).toBe("/");
    expect(safeReturnPath("https://evil.example.com")).toBe("/");
    expect(safeReturnPath(undefined)).toBe("/");
  });
});
