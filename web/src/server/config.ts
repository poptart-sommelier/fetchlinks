import path from "node:path";
import { pathToFileURL } from "node:url";

type Env = Partial<Record<string, string | undefined>>;

export type AppConfig = {
  fetchlinksDbPath: string;
  fetchlinksDbReadOnlyUri: string;
  controlDbPath: string;
};

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}


export function loadAppConfig(env: Env = process.env): AppConfig {
  const fetchlinksDbPath = readRequiredAbsolutePath(env, "FETCHLINKS_DB");
  // The control DB holds the admin-edited catalog (rss_feeds + subreddits
  // identity). It defaults to the data DB so single-host installs keep
  // using one physical file; set FETCHLINKS_CONTROL_DB to split it out for
  // a two-host (Pi ingest + VM web) deployment.
  const controlDbPath =
    readOptionalAbsolutePath(env, "FETCHLINKS_CONTROL_DB") ?? fetchlinksDbPath;

  return {
    fetchlinksDbPath,
    fetchlinksDbReadOnlyUri: toReadOnlySqliteUri(fetchlinksDbPath),
    controlDbPath,
  };
}

export function toReadOnlySqliteUri(dbPath: string): string {
  const trimmedPath = dbPath.trim();

  if (trimmedPath.length === 0) {
    throw new ConfigError("FETCHLINKS_DB must not be empty.");
  }

  const fileUrl = pathToFileURL(trimmedPath);
  fileUrl.searchParams.set("mode", "ro");

  return fileUrl.href;
}

function readRequiredAbsolutePath(env: Env, name: string): string {
  const value = env[name]?.trim();

  if (!value) {
    throw new ConfigError(
      `${name} is required. Set it to the absolute path of the fetchlinks SQLite database.`,
    );
  }

  if (!path.isAbsolute(value)) {
    throw new ConfigError(`${name} must be an absolute path.`);
  }

  return value;
}

function readOptionalAbsolutePath(env: Env, name: string): string | undefined {
  const value = env[name]?.trim();

  if (!value) {
    return undefined;
  }

  if (!path.isAbsolute(value)) {
    throw new ConfigError(`${name} must be an absolute path.`);
  }

  return value;
}