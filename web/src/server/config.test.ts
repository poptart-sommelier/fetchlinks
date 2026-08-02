import { describe, expect, it } from "vitest";

import {
  ConfigError,
  describeDatabaseUrl,
  loadAppConfig,
  parseDatabaseUrl,
} from "./config";

const VALID =
  "postgresql://web:secret@ep-example.eu-west-2.aws.neon.tech/fetchlinks?sslmode=require";

describe("loadAppConfig", () => {
  it("requires DATABASE_URL", () => {
    expect(() => loadAppConfig({})).toThrowError(ConfigError);
    expect(() => loadAppConfig({})).toThrowError(/DATABASE_URL is required/);
  });

  it("rejects a blank DATABASE_URL", () => {
    expect(() => loadAppConfig({ DATABASE_URL: "   " })).toThrowError(
      /DATABASE_URL is required/,
    );
  });

  it("returns the connection string unchanged", () => {
    expect(loadAppConfig({ DATABASE_URL: VALID })).toEqual({
      databaseUrl: VALID,
    });
  });

  it("trims surrounding whitespace", () => {
    expect(loadAppConfig({ DATABASE_URL: `  ${VALID}  ` }).databaseUrl).toBe(
      VALID,
    );
  });
});

describe("parseDatabaseUrl", () => {
  it("accepts both PostgreSQL schemes", () => {
    expect(parseDatabaseUrl("postgres://host/db").protocol).toBe("postgres:");
    expect(parseDatabaseUrl("postgresql://host/db").protocol).toBe(
      "postgresql:",
    );
  });

  it("rejects a value that is not a URL", () => {
    expect(() => parseDatabaseUrl("not a url")).toThrowError(
      /must be a valid connection URL/,
    );
  });

  // Parses as a URL with a "host:" scheme, so it has to be caught by the
  // scheme check rather than by URL parsing.
  it("rejects a bare host:port", () => {
    expect(() => parseDatabaseUrl("host:5432/fetchlinks")).toThrowError(
      /postgres:\/\/ or postgresql:\/\//,
    );
  });

  // A file path here would mean a SQLite setting was carried forward.
  it("rejects a non-PostgreSQL scheme", () => {
    expect(() => parseDatabaseUrl("file:///srv/fetchlinks.db")).toThrowError(
      /postgres:\/\/ or postgresql:\/\//,
    );
    expect(() => parseDatabaseUrl("mysql://host/db")).toThrowError(
      /postgres:\/\/ or postgresql:\/\//,
    );
  });

  it("rejects a URL with no database name", () => {
    expect(() => parseDatabaseUrl("postgres://host")).toThrowError(
      /must include a database name/,
    );
    expect(() => parseDatabaseUrl("postgres://host/")).toThrowError(
      /must include a database name/,
    );
  });

  it("rejects a URL with no host", () => {
    expect(() => parseDatabaseUrl("postgres:///fetchlinks")).toThrowError(
      /must include a host/,
    );
  });
});

describe("describeDatabaseUrl", () => {
  it("omits the credentials", () => {
    const described = describeDatabaseUrl(VALID);

    expect(described).toBe(
      "postgresql://ep-example.eu-west-2.aws.neon.tech/fetchlinks",
    );
    expect(described).not.toContain("secret");
  });
});
