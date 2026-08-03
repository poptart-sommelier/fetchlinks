import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Pool } from "pg";
import { afterAll, afterEach, beforeAll, describe } from "vitest";

import type { SqlClient } from "../sql";

/**
 * Integration-test harness for the query modules.
 *
 * These suites run against a real, disposable PostgreSQL rather than a fake.
 * The queries depend on ON CONFLICT, a LEFT JOIN across two schemas, boolean
 * and timestamptz coercion, and `to_char` formatting; a mock would only assert
 * that we passed it the string we already wrote.
 *
 * The connection goes through `pg` rather than Neon's HTTP driver, which only
 * speaks to Neon's endpoint. Both are PostgreSQL clients against the same
 * server, so the SQL under test is identical — what differs is the transport,
 * which `createSqlClient` owns and which Phase 5 exercises against real Neon.
 */
export const TEST_DATABASE_URL = process.env.FETCHLINKS_TEST_DATABASE_URL;

/**
 * `describe` when a test database is configured, `describe.skip` otherwise, so
 * a checkout with no database still runs the pure suites instead of failing.
 */
export const describePostgres = TEST_DATABASE_URL ? describe : describe.skip;

// 0003 (roles and grants) is deliberately not applied. These suites connect as
// the owner to arrange publisher-written rows; the web role's privileges are
// asserted by the publisher's own permission tests against the same migration.
const MIGRATIONS = ["0001_schemas_and_catalog.sql", "0002_content.sql"] as const;

// Truncated between tests to keep each case independent without paying to
// rebuild the schema. Add to this list when a migration adds a table.
const ALL_TABLES = [
  "content.post_urls",
  "content.posts",
  "content.rss_feed_health",
  "content.reddit_state",
  "content.bluesky_state",
  "content.mastodon_state",
  "content.follows_snapshots",
  "content.bluesky_follows",
  "content.mastodon_follows",
  "content.published_batches",
  "catalog.rss_feeds",
  "catalog.subreddits",
] as const;

export type PostgresFixture = {
  /** The client under test, wired to the throwaway database. */
  readonly sql: SqlClient;
  /** Arrange rows directly, including tables the web role cannot write. */
  exec(text: string, params?: readonly unknown[]): Promise<unknown[]>;
};

/**
 * Register the lifecycle hooks for a suite and return a live fixture. Call once
 * at the top of a `describePostgres` block.
 */
export function usePostgres(): PostgresFixture {
  let pool: Pool;

  const sql: SqlClient = {
    async query<T>(text: string, params: readonly unknown[] = []) {
      const result = await pool.query(text, params as unknown[]);
      return result.rows as T[];
    },
  };

  beforeAll(async () => {
    pool = new Pool({ connectionString: TEST_DATABASE_URL, max: 4 });
    await applyMigrations(pool);
  });

  afterEach(async () => {
    await pool.query(
      `TRUNCATE ${ALL_TABLES.join(", ")} RESTART IDENTITY CASCADE`,
    );
  });

  afterAll(async () => {
    await pool.end();
  });

  return {
    sql,
    exec: (text, params) => sql.query(text, params),
  };
}

async function applyMigrations(pool: Pool): Promise<void> {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const migrationsDir = path.resolve(here, "../../../../db/migrations");

  for (const name of MIGRATIONS) {
    await pool.query(await readFile(path.join(migrationsDir, name), "utf8"));
  }
}
