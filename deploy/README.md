# Raspberry Pi deployment

This directory installs the two halves of the Fetchlinks pipeline on a
Raspberry Pi. Nothing else lives here: the web GUI runs on Vercel and the
database on Neon, so there is no nginx, no Node, no TLS and no inbound port.

The Pi is kept for one reason. Several hundred RSS hosts throttle or block
datacentre IP ranges, and a residential connection is not subject to that. It
is a collection appliance, not a server.

## The two halves

**Collector** (`ingest/fetch_links.py`) reads config, credentials, a catalog
snapshot and its own resume state — all local files — fetches from every
enabled source, and writes one validated batch into the spool. It opens no
database and holds no database credential. It keeps working when Neon is
unreachable; batches simply queue.

**Publisher** (`ingest/publish_tool.py`) is the only thing that talks to
PostgreSQL. It drains queued batches into Neon, exports the catalog snapshot
the Collector reads, and applies retention.

The boundary is enforced by the units, not by convention: the collector unit
has no `EnvironmentFile`, so the Neon URL is not present in the process that
contacts several hundred untrusted websites.

## Files

```
deploy/
├── bootstrap.sh              idempotent installer; run as your login user
├── fetchlinks.pi.toml        collector config template, copied once
├── publisher.env.example     Neon URL template, copied once
└── systemd/
    ├── fetchlinks-collect.service   one collection cycle, no database
    ├── fetchlinks-collect.timer     every 30 minutes
    ├── fetchlinks-publish.service   drain the spool, then refresh the catalog
    ├── fetchlinks-publish.timer     hourly
    ├── fetchlinks-retain.service    apply the post age limit
    └── fetchlinks-retain.timer      weekly, Sunday
```

Units are templates. `bootstrap.sh` substitutes `__FETCHLINKS_APP_DIR__`,
`__FETCHLINKS_RUNTIME_DIR__` and `__FETCHLINKS_USER__`, then installs the
result into `/etc/systemd/system`. It fails loudly if a placeholder survives.

## Layout

Everything is one directory. There is no `/opt`, no `/var/lib`, no `/etc`
fragment to remember.

```
~/fetchlinks/                 the checkout
├── .venv/                    Python environment
└── runtime/                  everything mutable, gitignored
    ├── config/               fetchlinks.toml + source credentials (0600)
    ├── catalog/              catalog snapshot exported by the publisher
    ├── state/                collector resume state
    ├── outbox/               batch spool
    ├── logs/                 collector log
    └── publisher.env         Neon URL, publisher role only (0600)
```

`runtime/` sits inside the checkout and is gitignored, so `git pull` can never
disturb a queued batch or roll back a cursor. It is also the only path the
units are allowed to write to.

## Install

```bash
git clone https://github.com/poptart-sommelier/fetchlinks.git ~/fetchlinks
~/fetchlinks/deploy/bootstrap.sh
```

Run it as your normal login user, **not** with sudo — it escalates only for
apt and systemd, and it refuses to run as root. It will prompt for your sudo
password, so run it from an interactive terminal.

Then:

1. Put source credentials in `runtime/config/` as `reddit.json`,
   `bluesky.json` and `mastodon-infosec.json`. See [ingest/SETUP.md](../ingest/SETUP.md)
   for the JSON shape each source expects.
2. Put the publisher role's **direct** Neon URL in `runtime/publisher.env` —
   not the pooled one. See [publisher.env.example](publisher.env.example) for
   why: psycopg3 uses server-side prepared statements, which do not survive
   PgBouncer in transaction mode. Pooling is for the web app's many
   short-lived connections, not for one hourly batch.
3. Re-run `bootstrap.sh` to enable the publisher and retention timers, which it
   leaves disabled while `publisher.env` still holds the placeholder.

## Updating

```bash
cd ~/fetchlinks && git pull && ./deploy/bootstrap.sh
```

Nothing updates itself. The catalog syncs from Neon every hour, so feeds and
subreddits added in the web admin reach the Pi on their own — but code does
not. That is deliberate: an unattended `git pull` on the one host holding a
database credential would run anything merged to `master` within the hour.

`git pull` alone is enough for a Python-only change, because each timer run
starts a fresh process from the working tree. Re-run `bootstrap.sh` when
dependencies, systemd units, or the config template change; it is idempotent,
and it also removes units left over from the retired SQLite topologies.

### When the config template changes

`bootstrap.sh` never overwrites anything under `runtime/`, which is what keeps
your credentials and local choices safe across an update — but it means a
setting added to `deploy/fetchlinks.pi.toml` after install never reaches
`runtime/config/fetchlinks.toml`.

That gap is silent, because every setting has a default: the deployment simply
keeps using the old one. So `bootstrap.sh` compares the setting *names* in each
template against your copy and reports any the template has and you do not:

```
    kept   runtime/config/fetchlinks.toml
           note: deploy/fetchlinks.pi.toml adds [paths].runtime_dir
           your copy keeps its own values; add the setting by hand if you want it.
```

Values are never compared — a deployed file is supposed to differ from its
template. Add the named setting yourself, or if you have no local edits worth
keeping, delete the file and re-run `bootstrap.sh` to get a fresh copy.

To see the full picture at any time:

```bash
diff -u deploy/fetchlinks.pi.toml runtime/config/fetchlinks.toml
```

## A 32-bit userland wrinkle

Raspberry Pi OS ships a **64-bit kernel with a 32-bit userland**. `uname -m`
reports `aarch64`, but `dpkg --print-architecture` reports `armhf` and
Python's wheel tags are `armv8l`. `psycopg-binary` publishes no 32-bit ARM
wheels, so `psycopg[binary]` cannot be resolved at all.

`bootstrap.sh` detects this and installs the pure-Python `psycopg` against the
system `libpq5` instead. That costs some protocol-handling speed, which is
irrelevant for an hourly batch insert.

This cannot be expressed as a PEP 508 environment marker, because
`platform_machine` derives from `uname` and therefore reports `aarch64` — it
would select precisely the wheel that does not exist. Hence the explicit
branch in the installer.

## Day-to-day

```bash
systemctl list-timers 'fetchlinks-*'

journalctl -u fetchlinks-collect.service -n 50
journalctl -u fetchlinks-publish.service -n 50
journalctl -u fetchlinks-retain.service --since '30 days ago'

# Queue and database summary in one place.
~/fetchlinks/.venv/bin/python ~/fetchlinks/ingest/publish_tool.py \
  --config ~/fetchlinks/runtime/config/fetchlinks.toml status

# Inspect the spool without a database.
~/fetchlinks/.venv/bin/python ~/fetchlinks/ingest/spool_tool.py \
  --runtime-dir ~/fetchlinks/runtime status
```

Run either job by hand at any time; both are one-shots and safe to repeat.
Republishing a batch inserts nothing twice.

## Monitoring

There is none, deliberately. No new articles on the site is the symptom of
every failure that matters — collection stopped, publishing stopped, or the
database filled up and started refusing writes. A monitoring stack would only
restate what the front page already shows.

## Backups

There are none, deliberately. Collected posts are a one-month rolling window
that deletes itself, and losing all of them is an accepted outcome. The
irreplaceable part — the curated feed list — lives in Neon and is exported to
the Pi as `runtime/catalog/catalog.v1.json` on every publish, which is a
usable copy without anything scheduled to produce it.

## Timers, and why they are spaced the way they are

| Unit | Schedule | Touches Neon |
| --- | --- | --- |
| `fetchlinks-collect` | every 30 min | no |
| `fetchlinks-publish` | hourly at :07 | yes |
| `fetchlinks-retain` | Sunday 03:12 | yes |

Neon's compute cannot scale to zero in under five minutes, so every wake costs
at least five minutes of the monthly compute budget. That makes cadence — not
query volume — the thing that decides the bill, and it is why the catalog sync
shares the publisher's wake rather than running on its own timer. Collection is
free and can be as frequent as you like.
