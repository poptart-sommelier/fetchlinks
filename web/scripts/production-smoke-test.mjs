#!/usr/bin/env node

/**
 * Serve the production build against a real PostgreSQL and assert the public
 * page renders a row inserted moments earlier.
 *
 * Requires FETCHLINKS_SMOKE_DATABASE_URL. The application reaches the database
 * through Neon's HTTP driver, which only speaks to a Neon endpoint, so this has
 * to point at a Neon branch — the development branch, never production. That
 * constraint is the value of this check: it is the only one that exercises the
 * real driver, the real connection string and the real build together.
 *
 * The fixture is namespaced with a run id and removed in a finally block, so a
 * shared development branch is left as it was found.
 */

import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { access, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import net from "node:net";

import { Pool } from "pg";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const appDirectory = path.resolve(scriptDirectory, "..");
const migrationsDirectory = path.resolve(appDirectory, "..", "db", "migrations");

const databaseUrl = process.env.FETCHLINKS_SMOKE_DATABASE_URL?.trim();

if (!databaseUrl) {
  throw new Error(
    "FETCHLINKS_SMOKE_DATABASE_URL is required. Set it to the Neon development " +
      "branch connection string (never the production branch).",
  );
}

await ensureBuildExists();

const runId = randomUUID();
const description = `Production smoke post ${runId}`;
const pool = new Pool({ connectionString: databaseUrl, max: 2 });
let productionServer;

try {
  await applyMigrations(pool);
  await seedFixture(pool, runId, description);

  const port = await getAvailablePort();
  const pageUrl = `http://127.0.0.1:${port}/?q=${runId}`;
  const command = process.platform === "win32" ? "npm.cmd" : "npm";
  const outputChunks = [];

  productionServer = spawn(
    command,
    ["run", "start", "--", "--hostname", "127.0.0.1", "--port", String(port)],
    {
      cwd: appDirectory,
      detached: process.platform !== "win32",
      env: { ...process.env, DATABASE_URL: databaseUrl },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  productionServer.stdout.on("data", (chunk) => {
    outputChunks.push(chunk.toString());
  });

  productionServer.stderr.on("data", (chunk) => {
    outputChunks.push(chunk.toString());
  });

  const html = await waitForPage(pageUrl, productionServer, outputChunks);

  assertIncludes(html, description);
  assertIncludes(html, "post-source-action");
  assertIncludes(html, "example.com");

  console.log(`Production smoke test passed at ${pageUrl}`);
} finally {
  if (productionServer) {
    await stopServer(productionServer);
  }

  await removeFixture(pool, runId).catch((error) => {
    console.error(`Failed to remove the smoke fixture for ${runId}:`, error);
  });
  await pool.end();
}

async function ensureBuildExists() {
  try {
    await access(path.join(appDirectory, ".next", "BUILD_ID"));
  } catch {
    throw new Error(
      "Production smoke test requires a Next.js build. Run npm run build first.",
    );
  }
}

async function applyMigrations(pool) {
  for (const name of ["0001_schemas_and_catalog.sql", "0002_content.sql"]) {
    await pool.query(
      await readFile(path.join(migrationsDirectory, name), "utf8"),
    );
  }
}

async function seedFixture(pool, runId, description) {
  const { rows } = await pool.query(
    `INSERT INTO content.posts
       (unique_id, source, source_type, author, description, direct_link, posted_at)
     VALUES ($1, $2, 'rss', 'Production Source', $3, $4, now())
     RETURNING post_id`,
    [
      `smoke-${runId}`,
      "https://example.com/source",
      description,
      "https://example.com/source-post",
    ],
  );

  await pool.query(
    `INSERT INTO content.post_urls (post_id, position, url, url_hash)
     VALUES ($1, 0, $2, $3)`,
    [rows[0].post_id, "https://example.com/article", `smoke-url-${runId}`],
  );
}

async function removeFixture(pool, runId) {
  // post_urls cascades on delete.
  await pool.query("DELETE FROM content.posts WHERE unique_id = $1", [
    `smoke-${runId}`,
  ]);
}

async function getAvailablePort() {
  const portServer = net.createServer();

  return await new Promise((resolve, reject) => {
    portServer.once("error", reject);
    portServer.listen(0, "127.0.0.1", () => {
      const address = portServer.address();

      portServer.close(() => {
        if (!address || typeof address === "string") {
          reject(
            new Error(
              "Could not reserve a local port for the production smoke test.",
            ),
          );
          return;
        }

        resolve(address.port);
      });
    });
  });
}

async function waitForPage(pageUrl, childProcess, outputChunks) {
  const timeoutAt = Date.now() + 15000;
  let childExit;
  let lastError;

  childProcess.once("exit", (code, signal) => {
    childExit = { code, signal };
  });

  while (Date.now() < timeoutAt) {
    if (childExit) {
      throw new Error(
        `Production server exited before responding: ${formatExit(childExit)}\n${formatOutput(outputChunks)}`,
      );
    }

    try {
      const response = await fetch(pageUrl, {
        signal: AbortSignal.timeout(1000),
      });

      if (response.ok) {
        return await response.text();
      }

      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }

    await delay(250);
  }

  throw new Error(
    `Production server did not respond at ${pageUrl}: ${String(lastError)}\n${formatOutput(outputChunks)}`,
  );
}

function assertIncludes(value, expected) {
  if (!value.includes(expected)) {
    throw new Error(
      `Production smoke response did not include ${JSON.stringify(expected)}.`,
    );
  }
}

async function stopServer(childProcess) {
  if (childProcess.exitCode !== null || childProcess.signalCode !== null) {
    return;
  }

  const exitPromise = new Promise((resolve) => {
    childProcess.once("exit", () => resolve(true));
  });

  signalServer(childProcess, "SIGTERM");

  const stopped = await Promise.race([
    exitPromise,
    delay(5000).then(() => false),
  ]);

  if (!stopped) {
    signalServer(childProcess, "SIGKILL");
    await Promise.race([exitPromise, delay(2000)]);
  }
}

function signalServer(childProcess, signal) {
  try {
    if (process.platform === "win32" || !childProcess.pid) {
      childProcess.kill(signal);
      return;
    }

    process.kill(-childProcess.pid, signal);
  } catch (error) {
    if (
      error &&
      typeof error === "object" &&
      "code" in error &&
      error.code === "ESRCH"
    ) {
      return;
    }

    throw error;
  }
}

function formatExit({ code, signal }) {
  return `code=${String(code)} signal=${String(signal)}`;
}

function formatOutput(outputChunks) {
  return outputChunks.join("").slice(-4000);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
