import { neon } from "@neondatabase/serverless";

import { loadAppConfig, type AppConfig } from "./config";

type Env = Partial<Record<string, string | undefined>>;

export type DbConfig = Pick<AppConfig, "databaseUrl">;

/**
 * The one thing every query module needs: run parameterised SQL, get rows back.
 *
 * Narrow on purpose. Neon's HTTP driver is a good fit for Vercel functions —
 * no connection to establish, no pool to drain — but it only speaks to Neon's
 * endpoint, so tests cannot point it at a throwaway PostgreSQL. Expressing the
 * dependency as this port lets the tests drive the same SQL through a plain
 * TCP client while production uses the real driver. What that leaves untested
 * is the adapter below, which is small enough to read in one go; what it buys
 * is that every query is checked against a real PostgreSQL rather than a mock's
 * idea of one.
 *
 * Every mutation returns its affected rows via RETURNING rather than relying on
 * a row count, so the port needs no result metadata.
 */
export type SqlClient = {
  query<T>(text: string, params?: readonly unknown[]): Promise<T[]>;
};

export function createSqlClient(config: DbConfig): SqlClient {
  const sql = neon(config.databaseUrl);

  return {
    async query<T>(text: string, params: readonly unknown[] = []): Promise<T[]> {
      const rows = await sql.query(text, params as unknown[]);

      return rows as T[];
    },
  };
}

const clientsByUrl = new Map<string, SqlClient>();

/**
 * Cached per connection string. Neon's HTTP driver holds no socket, so this is
 * about not re-validating configuration on every render rather than about
 * reusing a connection.
 */
export function getSqlClient(env: Env = process.env): SqlClient {
  const { databaseUrl } = loadAppConfig(env);
  const existing = clientsByUrl.get(databaseUrl);

  if (existing) {
    return existing;
  }

  const client = createSqlClient({ databaseUrl });
  clientsByUrl.set(databaseUrl, client);

  return client;
}

/**
 * Collects bind values and hands back the `$n` placeholder for each one.
 *
 * The queries below assemble WHERE clauses from optional filters, and hand
 * numbering across a growing clause list is exactly the kind of bookkeeping
 * that silently binds the wrong value.
 */
export class SqlParams {
  private readonly values: unknown[] = [];

  next(value: unknown): string {
    this.values.push(value);

    return `$${this.values.length}`;
  }

  toArray(): unknown[] {
    return [...this.values];
  }
}

/**
 * Render a `timestamptz` as an ISO-8601 UTC string.
 *
 * Formatting in SQL rather than converting whatever object a driver decides to
 * return keeps the read models driver-independent. It also fixes a real defect
 * inherited from SQLite, which stored `YYYY-MM-DD HH:MM:SS` with no zone: the
 * browser parsed those as *local* time, so every displayed timestamp was off by
 * the viewer's UTC offset.
 */
export function utcIso(expression: string): string {
  return `to_char(${expression} AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')`;
}

/**
 * Escape a user-supplied search term for use inside a LIKE pattern.
 *
 * Paired with `ESCAPE '\'` in the SQL, so a search for "100%" matches a literal
 * percent sign instead of everything.
 */
export function escapeLikeValue(value: string): string {
  return value
    .replaceAll("\\", "\\\\")
    .replaceAll("%", "\\%")
    .replaceAll("_", "\\_");
}
