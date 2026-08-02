type Env = Partial<Record<string, string | undefined>>;

export type AppConfig = {
  databaseUrl: string;
};

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

const ALLOWED_PROTOCOLS = new Set(["postgres:", "postgresql:"]);

export function loadAppConfig(env: Env = process.env): AppConfig {
  return { databaseUrl: readDatabaseUrl(env, "DATABASE_URL") };
}

/**
 * Parse and validate a PostgreSQL connection string.
 *
 * The check is deliberately strict about scheme, host and database name. A
 * malformed URL that reaches the driver instead surfaces as a connection error
 * at request time, on a page that has already begun rendering; validating here
 * turns a misconfigured deployment into an immediate, named failure.
 */
export function parseDatabaseUrl(value: string): URL {
  let url: URL;

  try {
    url = new URL(value);
  } catch {
    throw new ConfigError("DATABASE_URL must be a valid connection URL.");
  }

  if (!ALLOWED_PROTOCOLS.has(url.protocol)) {
    throw new ConfigError(
      "DATABASE_URL must use the postgres:// or postgresql:// scheme.",
    );
  }

  if (!url.hostname) {
    throw new ConfigError("DATABASE_URL must include a host.");
  }

  if (url.pathname.replace(/^\//, "") === "") {
    throw new ConfigError("DATABASE_URL must include a database name.");
  }

  return url;
}

/**
 * A connection string with the credentials removed, safe for a log line or an
 * error message. Nothing should ever surface the raw URL.
 */
export function describeDatabaseUrl(value: string): string {
  const url = parseDatabaseUrl(value);

  return `${url.protocol}//${url.hostname}${url.pathname}`;
}

function readDatabaseUrl(env: Env, name: string): string {
  const value = env[name]?.trim();

  if (!value) {
    throw new ConfigError(
      `${name} is required. Set it to the PostgreSQL connection string for the fetchlinks database.`,
    );
  }

  parseDatabaseUrl(value);

  return value;
}
