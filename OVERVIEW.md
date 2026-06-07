# Fetchlinks overview

Fetchlinks ingests links shared on RSS, Reddit, Bluesky, and Mastodon into a
single SQLite database and serves them through a server-rendered Next.js web
app. It runs on one small Ubuntu VM with the smallest possible operational
surface.

For how to deploy and operate it on a VM, see [deploy/README.md](deploy/README.md).
For forward-looking work and history, see `PLAN.md` (local, untracked).

## Goal

Deploy Fetchlinks on a single Ubuntu 24.04 VM with the smallest possible
operational surface:

- Stand up a fresh VM in Azure (manual, a few clicks, once or twice a year).
- SSH in, run **one script**, get a working system. Run a second, separate
  script when DNS exists and you want public TLS.
- Re-run either script to update or recover from drift.

No Docker. No Ansible. No container registry. No managed database.

## Architecture

```text
Azure VM (Ubuntu 24.04 LTS, B1ms, East US)
  ├─ nginx + certbot                            host TLS reverse proxy (optional, via tls.sh)
  ├─ Node.js 24                                 runs `next start`
  ├─ Python 3.12 + venv at <checkout>/.venv
  ├─ <checkout>                            git checkout + runtime home
  ├─ <checkout>/ingest/db/fetchlinks.db    SQLite (WAL)
  ├─ <checkout>/ingest/data/config/        fetchlinks.toml + rss_feeds.txt
  ├─ <checkout>/ingest/data/logs/          ingest logs
  ├─ <checkout>/web/.env.production        web env + admin Basic auth
  ├─ systemd: fetchlinks-web.service            127.0.0.1:3000 (Next.js)
  ├─ systemd: fetchlinks-ingest.{service,timer} oneshot Python ingest, every 30 min
  ├─ systemd: fetchlinks-retain.{service,timer} weekly retention + conditional VACUUM
  └─ systemd: fetchlinks-export-rss-feeds.{service,timer} 5-minute DB → rss_feeds.txt snapshot
```

Request path: `Internet -> nginx:443 (TLS) -> 127.0.0.1:3000 -> SQLite`
Ingest path:  `timer -> venv python fetch_links.py -> SQLite`
Admin path:   `Internet -> nginx:443 -> /admin/* -> HTTP Basic -> SQLite (read-write)`

Public site is read-only; the `/admin` route is an index of admin
sub-pages (currently just `/admin/feeds` for the RSS feed table). All
`/admin/*` routes open the DB read-write to manage their respective
state. Auth is HTTP Basic via env vars
(`FETCHLINKS_ADMIN_USER` / `FETCHLINKS_ADMIN_PASS`) read by `web/src/proxy.ts`.

## Cost target

| Item | Approx GBP/mo |
|---|---|
| B1ms VM (East US) | £11.62 |
| Standard SSD (~32 GB) | ~£2.50 |
| Static IP | ~£2.75 |
| **Total** | **~£17/mo (~$21 USD)** |

## Repository layout

```text
deploy/
├── README.md                          how to deploy + operate on a VM
├── bootstrap.sh                       app + services + firewall installer / updater
├── tls.sh                             nginx + Let's Encrypt provisioner (separate, idempotent)
├── nginx/fetchlinks-web.conf.example  nginx site template (rendered by tls.sh)
└── systemd/
    ├── fetchlinks-web.service                         Next.js webapp
    ├── fetchlinks-ingest.{service,timer}              ingest (every 30 min)
    ├── fetchlinks-retain.{service,timer}              retention (weekly)
    └── fetchlinks-export-rss-feeds.{service,timer}    feed snapshot (every 5 min)

ingest/                                Python ingest package + tests
  ├── data/config/fetchlinks.toml      runtime config used in dev + production
  ├── data/config/rss_feeds.txt        seed file + 5-minute DB snapshot
  └── db/fetchlinks.db                 SQLite DB (ignored)
web/                                   Next.js webapp (TypeScript, vitest)
  ├── .env.production.example          production web env template
  ├── src/app/page.tsx                 public posts listing
  ├── src/app/admin/page.tsx           admin index (links to sub-pages)
  ├── src/app/admin/feeds/             admin UI for the rss_feeds table
  ├── src/server/                      DB helpers, config loader
  └── src/proxy.ts                     /admin/* HTTP Basic gate (Next 16 proxy convention)
```

## Security

- SSH: key-only (Azure default), root login disabled (Ubuntu default).
- Firewall: `ufw` deny inbound, allow 22/80/443 only.
- Services run as `fetchlinks`, never root.
- API credentials live wherever `ingest/data/config/fetchlinks.toml` points;
  bootstrap does not create, copy, chmod, or otherwise manage them. Web admin
  credentials live in ignored `web/.env.production`.
- Web admin (`/admin/*`) is gated by HTTP Basic against
  `FETCHLINKS_ADMIN_USER` / `FETCHLINKS_ADMIN_PASS` in
  `<checkout>/web/.env.production`. Constant-time credential compare. If either
  var is unset, `/admin/*` returns 503 instead of granting access.
- TLS via certbot with auto-renewal timer.
- Security updates via `unattended-upgrades`.

## Local development

- `npm run dev` in `web/` (or the **Webapp: Dev Server** VS Code task —
  sets `FETCHLINKS_DB`, `FETCHLINKS_ADMIN_USER`, `FETCHLINKS_ADMIN_PASS`).
- Host Python venv for ingest, run via the **Ingest: Run** task.
- `npm run validate` in `web/` for lint + typecheck + tests + build.
- `pytest` (under `.venv`) for the Python suite.

## Baseline facts

- Reddit, Bluesky, Mastodon, and RSS are **all live in production** with
  credentials at the paths referenced by `ingest/data/config/fetchlinks.toml`.
  New credentialed sources need config entries and matching external files.
- The DB is the live source of truth for posts, URLs, and `rss_feeds`.
  `rss_feeds.txt` is seed input on first install and is then refreshed from
  the DB every 5 minutes for review, backup, and occasional repo commits.
- `bootstrap.sh` is idempotent and is also the upgrade path. There is no
  separate "deploy" or "release" script.
- The web app is server-rendered, forms-only (no client JS), opens the DB
  read-only on the public pages and read-write on `/admin/*`. SQLite is
  in WAL mode; concurrent ingest writes don't block reads.
