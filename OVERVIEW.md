# Fetchlinks overview

Fetchlinks collects links shared on RSS, Reddit, Bluesky and Mastodon, stores
them in PostgreSQL, and serves them through a server-rendered Next.js web app.

It runs on managed services with one deliberate exception. The web app is on
Vercel and the database on Neon, both free tier. Collection stays on a
Raspberry Pi at home, because several hundred RSS hosts throttle or block
datacentre IP ranges and a residential connection is not subject to that.

For deploying and operating the Pi, see [deploy/README.md](deploy/README.md).

## Architecture

```text
Raspberry Pi (home, residential IP)          Neon (eu-west-2)          Vercel (lhr1)
  fetchlinks-collect.timer  30 min             PostgreSQL 18             Next.js
    fetch_links.py                               catalog.*                 public pages
      RSS / Reddit / Bluesky / Mastodon          content.*                 /flightdeck/* (Basic auth)
      └─> runtime/outbox/   batch spool
  fetchlinks-publish.timer  hourly
    publish_tool.py publish       ───────────>  insert batches
    publish_tool.py sync-catalog  <───────────   export catalog snapshot
  fetchlinks-retain.timer   weekly
    publish_tool.py retain        ───────────>  delete posts past one month
```

Read path: `Internet -> Vercel (lhr1) -> Neon HTTP driver -> PostgreSQL`
Write path: `Pi -> psycopg over TLS -> PostgreSQL`
Admin path: `Internet -> Vercel -> /flightdeck/* -> HTTP Basic -> PostgreSQL`

## The collector/publisher split

This is the central design decision, and everything else follows from it.

**Collector** — `ingest/fetch_links.py` and the source modules. Reads config,
credentials, a catalog snapshot and its own resume state, all local files.
Fetches from every enabled source and writes one validated batch to the spool.
It contains no SQL, no table names, no driver import and no database
configuration.

**Publisher** — `ingest/publish_tool.py` and `ingest/publisher/`. The only code
that opens a database. Drains queued batches, exports the catalog snapshot the
Collector reads, applies migrations and enforces retention.

They meet at a versioned on-disk batch contract (`ingest/pipeline/`, schemas in
`ingest/schemas/`), not at a function call. That buys three things:

- The Pi holds no database credential in the process that contacts several
  hundred untrusted websites. The boundary is enforced by which systemd unit
  reads `publisher.env`, not by convention.
- A database outage costs nothing. Batches queue on disk and drain later. The
  collector never stops and never loses work.
- Collection is portable. It runs anywhere with a filesystem; the destination
  is somebody else's problem.

Publishing is idempotent. Replaying a batch produces no duplicate posts,
no repeated health increments and no checkpoint regression, so a crash between
"inserted" and "recorded as published" is safe to retry.

## Schemas and roles

Three schemas, and the split matters:

- **`catalog`** — feed and subreddit identity, and their on/off state. Owned by
  the web admin. The Publisher reads it and exports it to the Pi.
- **`content`** — everything collection produces: posts, URLs, feed health,
  source checkpoints, follows snapshots, and the published-batch ledger.
- **`app`** — schema migration bookkeeping.

Two runtime roles, neither of which is the database owner and neither of which
can perform DDL:

- `fetchlinks_web` — used by Vercel. Reads everything; writes only the catalog
  tables the admin UI manages.
- `fetchlinks_publisher` — used by the Pi. Writes `content`, reads `catalog`.

Migrations live in `db/migrations/` and are applied by the owner role, never by
a running service.

## Cost

| Item | GBP/mo |
| --- | --- |
| Vercel Hobby | £0 |
| Neon Free | £0 |
| Raspberry Pi (already owned, ~3 W) | ~£0.60 electricity |

Two free-tier limits actually bind, and both shaped the design:

- **Compute: 100 CU-hours/month.** Neon cannot scale to zero in under five
  minutes, so every wake costs at least five minutes. Cadence, not query
  volume, decides the bill. Hourly publishing lands near 15 CU-hours; every 15
  minutes would be about 61. The catalog sync shares the publisher's wake for
  the same reason.
- **Storage: 0.5 GB.** Retention is the only control over it, which is why the
  post window is one month rather than three.

## Repository layout

```text
db/migrations/                     PostgreSQL schema, applied by the owner role
deploy/                            Raspberry Pi installer and systemd units
├── bootstrap.sh                   idempotent installer; also the upgrade path
├── fetchlinks.pi.toml             collector config template
├── publisher.env.example          Neon URL template (publisher role)
└── systemd/                       collect / publish / retain units and timers

ingest/                            Python, collector + publisher
├── fetch_links.py                 collector entry point; no database
├── publish_tool.py                publisher CLI: migrate, bootstrap-catalog,
│                                  sync-catalog, publish, retain, status
├── spool_tool.py                  inspect the batch spool without a database
├── pipeline/                      batch contract, spool, catalog, state
├── publisher/                     connection, migrations, insertion, retention
├── schemas/                       versioned batch JSON Schemas
└── data/config/                   development config and seed lists

web/                               Next.js, TypeScript, vitest
├── src/app/page.tsx               public posts listing
├── src/app/flightdeck/            admin index and the feeds table
├── src/server/sql.ts              SqlClient port: Neon HTTP driver or pg
└── src/proxy.ts                   /flightdeck/* HTTP Basic gate
```

`web/src/server/sql.ts` exists because Neon's HTTP driver suits Vercel but only
speaks to Neon. Tests drive identical SQL through `pg` against a real
PostgreSQL instance. That leaves a thin adapter untested and buys real-database
coverage of every query, which is the better trade.

## Runtime layout on the Pi

Everything lives in one directory, with all mutable state under a single
gitignored `runtime/`:

```text
~/fetchlinks/                 the checkout
├── .venv/
└── runtime/
    ├── config/               fetchlinks.toml + source credentials (0600)
    ├── catalog/              catalog snapshot exported by the publisher
    ├── state/                collector resume state
    ├── outbox/               batch spool
    ├── logs/
    └── publisher.env         Neon URL (0600)
```

Because `runtime/` is gitignored, `git pull` can never disturb a queued batch
or roll back a cursor.

## Security

- The Pi accepts no inbound connections beyond SSH and runs no web server.
- The collector unit has no `EnvironmentFile`, so the database URL is absent
  from the process that fetches from untrusted sites.
- Neither runtime role can perform DDL or write outside its own surface. This
  is asserted against a real instance, not assumed.
- Source credentials are `0600` under `runtime/config/`, never in the repo.
- `/flightdeck/*` is gated by HTTP Basic with a constant-time compare. If either
  credential variable is unset the route returns 503 rather than opening.
- The admin route is not called `/admin` so that drive-by scanners probing that
  path find nothing. This is noise reduction and not a security control: the
  name is visible in this public repository, and HTTP Basic remains the thing
  actually standing in the way.
- Vercel preview deployments are pinned to a separate Neon branch, so a preview
  cannot read or write production data.
- TLS to the database is enforced by the connection string.

## Operations

No backups and no alerting, both deliberate.

Posts are a one-month rolling window that deletes itself, and losing all of
them is an accepted outcome, so nothing is backed up. The one irreplaceable
asset — the curated feed list — is exported to the Pi on every publish as a
side effect of normal operation.

No new articles on the site is the symptom of every failure that matters:
collection stopped, publishing stopped, or the database filled and started
refusing writes. Monitoring would only restate what the front page shows.
Services log to journald; `publish_tool.py status` reports the queue and the
database on demand.

## Local development

- `npm run dev` in `web/`; `npm run validate` for lint, typecheck, tests and
  build.
- Web tests need a real PostgreSQL instance — see [web/README.md](web/README.md).
- Python: `python -m unittest discover -s tests -t .` from `ingest/`.
- The collector runs against a local spool with no database at all, which is
  the quickest way to exercise a source end to end.
