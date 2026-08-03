# Fetchlinks overview

Fetchlinks ingests links shared on RSS, Reddit, Bluesky, and Mastodon into
SQLite and serves them through a server-rendered Next.js web app. It runs on a
single small Ubuntu VM by default, with the smallest possible operational
surface; ingest can optionally be split onto a home Raspberry Pi (see
Architecture below).

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

Fetchlinks runs in one of two topologies. **Single-host** (the default) keeps
everything on one VM with one SQLite file. The **two-host split** moves ingest
to a home Raspberry Pi (residential IP, to escape Azure-IP RSS throttling)
while the web GUI stays on the VM; the two sides share two SQLite files, one
writer each. The split is opt-in — leaving `[paths].control_db` unset collapses
back to a single physical file.

### Single-host (default)

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

### Two-host split (opt-in)

Two SQLite files, one writer each:

- **control.db (VM-owned)** — feed/subreddit *identity + on/off*. The web admin
  writes it; the Pi only reads a pulled copy.
- **data.db (Pi-owned)** — everything ingest produces: posts, per-feed health,
  follows snapshots, ingest cursors. The Pi writes it; the web only reads it.

Cross-file relations key on natural keys (`normalized_url`, `normalized_name`,
post `unique_id_string`, Bluesky `did`) — never autoincrement ids, which don't
match across separate files. The web admin opens control.db read-write and
`ATTACH`es a read-only data.db so membership + health show as one row.

```text
Home Pi (ingest role)                         Azure VM (web role)
  fetchlinks-sync.timer (30 min)                fetchlinks-web.service (Next.js)
    1. rsync pull  control.db  <───────────────  control.db (canonical; admin writes)
    2. fetch_links.py  -> data.db
    3. retain.py       -> data.db (Pi only)
    4. sqlite3 VACUUM INTO snapshot
    5. rsync push  data.db     ───────────────>  data.db (replica; atomic rename; web reads)
```

Transport is **Pi-initiated SSH/rsync** — no inbound to the home network and no
new service on the VM. The VM restricts the Pi's key to rsync within one
directory (see `deploy/sync/authorized_keys.example`). The web opens data.db
per request read-only, so an atomic rename swap needs no web restart. A
one-cycle lag is acceptable: adding/removing a feed is instant (read from
control.db), and its health columns fill in on the next data.db ship.
Retention runs **only on the Pi** because the VM's data.db is a pure replica.
Auto-disable-on-failure is dropped (the Pi can't write VM-owned `enabled`);
the Pi counts `consecutive_failures` + `last_error` and the admin surfaces
failing feeds for manual removal.

See [deploy/sync/README.md](deploy/sync/README.md) for setup.

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
├── bootstrap.sh                       app + services + firewall installer / updater (FETCHLINKS_ROLE=all|web|ingest)
├── tls.sh                             nginx + Let's Encrypt provisioner (separate, idempotent)
├── nginx/fetchlinks-web.conf.example  nginx site template (rendered by tls.sh)
├── sync/                              two-host (Pi ingest + VM web) sync layer
│   ├── README.md                      two-host setup + cycle docs
│   ├── fetchlinks-sync.sh             Pi cycle: pull control.db, ingest, retain, snapshot, push data.db
│   ├── fetchlinks-sync.env.example    sync service environment (VM target, paths)
│   └── authorized_keys.example        VM-side rsync-restricted SSH key
└── systemd/
    ├── fetchlinks-web.service                         Next.js webapp (web role)
    ├── fetchlinks-ingest.{service,timer}              ingest, every 30 min (single-host)
    ├── fetchlinks-retain.{service,timer}              retention, weekly (single-host)
    ├── fetchlinks-export-rss-feeds.{service,timer}    feed snapshot, every 5 min (web role)
    └── fetchlinks-sync.{service,timer}                Pi sync cycle, every 30 min (ingest role)

ingest/                                Python ingest package + tests
  ├── data/config/fetchlinks.toml      runtime config used in dev + production
  ├── data/config/rss_feeds.txt        seed file + 5-minute DB snapshot
  └── db/fetchlinks.db                 SQLite DB (single-host; two-host uses control.db + data.db)
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
- Firewall: `ufw` deny inbound. The web role allows 22/80/443; the ingest Pi
  allows only 22 (and initiates all sync outbound). The Pi's SSH key on the VM
  is restricted to rsync within a single directory (`restrict` + rrsync).
- Services run as `fetchlinks`, never root.
- API credentials live wherever `ingest/data/config/fetchlinks.toml` points;
  bootstrap can install missing enabled-source credential files during first
  setup. Web admin credentials live in ignored `web/.env.production`.
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

- Reddit, Bluesky, Mastodon, and RSS are implemented, but Fetchlinks has not yet
  been deployed in production. Credentialed sources need config entries and
  matching external files.
- When the current SQLite topology is run, the DB is the runtime source of truth
  for posts, URLs, and `rss_feeds`.
  In the two-host split, feed/subreddit identity + on/off live in VM-owned
  control.db and everything ingest produces (posts, health, follows, cursors)
  lives in Pi-owned data.db, joined on natural keys.
  `rss_feeds.txt` is seed input on first install and is then refreshed from
  the DB every 5 minutes for review, backup, and occasional repo commits.
- `bootstrap.sh` is idempotent and is also the upgrade path. There is no
  separate "deploy" or "release" script.
- The web app is server-rendered, forms-only (no client JS), opens the DB
  read-only on the public pages and read-write on `/admin/*`. SQLite is
  in WAL mode; concurrent ingest writes don't block reads.
