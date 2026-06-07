import { describe, expect, it } from "vitest";

import { ConfigError, loadAppConfig, toReadOnlySqliteUri } from "./config";

describe("loadAppConfig", () => {
  it("requires FETCHLINKS_DB", () => {
    expect(() => loadAppConfig({})).toThrowError(ConfigError);
    expect(() => loadAppConfig({})).toThrowError(/FETCHLINKS_DB is required/);
  });

  it("rejects an empty FETCHLINKS_DB", () => {
    expect(() => loadAppConfig({ FETCHLINKS_DB: "   " })).toThrowError(
      /FETCHLINKS_DB is required/,
    );
  });

  it("rejects a relative FETCHLINKS_DB", () => {
    expect(() =>
      loadAppConfig({ FETCHLINKS_DB: "data/fetchlinks.db" }),
    ).toThrowError(/FETCHLINKS_DB must be an absolute path/);
  });

  it("loads the configured database path and read-only SQLite URI", () => {
    const config = loadAppConfig({
      FETCHLINKS_DB: "/home/ubuntu/fetchlinks/ingest/db/fetchlinks.db",
    });

    expect(config).toEqual({
      fetchlinksDbPath: "/home/ubuntu/fetchlinks/ingest/db/fetchlinks.db",
      fetchlinksDbReadOnlyUri: "file:///home/ubuntu/fetchlinks/ingest/db/fetchlinks.db?mode=ro",
    });
  });
});


describe("toReadOnlySqliteUri", () => {
  it("encodes paths for SQLite URI use", () => {
    expect(toReadOnlySqliteUri("/tmp/fetch links #1.db")).toBe(
      "file:///tmp/fetch%20links%20%231.db?mode=ro",
    );
  });
});