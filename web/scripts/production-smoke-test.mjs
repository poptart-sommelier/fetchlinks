#!/usr/bin/env node

import { spawn } from "node:child_process";
import { access, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import net from "node:net";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const appDirectory = path.resolve(scriptDirectory, "..");

await ensureBuildExists();

const temporaryDirectory = await mkdtemp(path.join(tmpdir(), "fetchlinks-production-"));
const databasePath = path.join(temporaryDirectory, "fetchlinks.db");
let productionServer;

try {
  createFixtureDatabase(databasePath);

  const port = await getAvailablePort();
  const pageUrl = `http://127.0.0.1:${port}/`;
  const command = process.platform === "win32" ? "npm.cmd" : "npm";
  const outputChunks = [];

  productionServer = spawn(
    command,
    ["run", "start", "--", "--hostname", "127.0.0.1", "--port", String(port)],
    {
      cwd: appDirectory,
      detached: process.platform !== "win32",
      env: { ...process.env, FETCHLINKS_DB: databasePath },
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

  assertIncludes(html, "Production smoke post");
  assertIncludes(html, "Production Source");
  assertIncludes(html, ">link 1</a>");
  assertIncludes(html, "post-source-action");

  console.log(`Production smoke test passed at ${pageUrl}`);
} finally {
  if (productionServer) {
    await stopServer(productionServer);
  }

  await rm(temporaryDirectory, { force: true, recursive: true });
}

async function ensureBuildExists() {
  try {
    await access(path.join(appDirectory, ".next", "BUILD_ID"));
  } catch {
    throw new Error("Production smoke test requires a Next.js build. Run npm run build first.");
  }
}

function createFixtureDatabase(dbPath) {
  const database = new DatabaseSync(dbPath);

  try {
    database.exec(`
      CREATE TABLE posts (
        idx INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        author TEXT,
        description TEXT,
        direct_link TEXT,
        date_created TEXT NOT NULL,
        unique_id_string TEXT NOT NULL
      );

      CREATE TABLE post_urls (
        idx INTEGER PRIMARY KEY,
        post_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        url TEXT NOT NULL,
        url_hash TEXT NOT NULL,
        unshortened_url TEXT,
        FOREIGN KEY (post_id) REFERENCES posts(idx)
      );
    `);

    database
      .prepare(
        `INSERT INTO posts (
          idx,
          source,
          author,
          description,
          direct_link,
          date_created,
          unique_id_string
        ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        1,
        "https://example.com/source",
        "Production Source",
        "Production smoke post",
        "https://example.com/source-post",
        "2026-04-28T12:00:00Z",
        "production-smoke-post",
      );

    database
      .prepare(
        `INSERT INTO post_urls (
          idx,
          post_id,
          position,
          url,
          url_hash,
          unshortened_url
        ) VALUES (?, ?, ?, ?, ?, ?)`,
      )
      .run(
        1,
        1,
        0,
        "https://example.com/article",
        "production-smoke-url",
        null,
      );
  } finally {
    database.close();
  }
}

async function getAvailablePort() {
  const portServer = net.createServer();

  return await new Promise((resolve, reject) => {
    portServer.once("error", reject);
    portServer.listen(0, "127.0.0.1", () => {
      const address = portServer.address();

      portServer.close(() => {
        if (!address || typeof address === "string") {
          reject(new Error("Could not reserve a local port for the production smoke test."));
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
      const response = await fetch(pageUrl, { signal: AbortSignal.timeout(1000) });

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
    throw new Error(`Production smoke response did not include ${JSON.stringify(expected)}.`);
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
    if (error && typeof error === "object" && "code" in error && error.code === "ESRCH") {
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
