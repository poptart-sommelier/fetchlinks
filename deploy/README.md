# Fetchlinks VM deployment

This directory contains everything needed to stand up Fetchlinks on a single
Ubuntu 24.04 VM.

## Files

```
deploy/
├── bootstrap.sh                       one-shot installer / updater (run on VM)
├── tls.sh                             nginx + Let's Encrypt provisioner (run after bootstrap)
├── nginx/
│   └── fetchlinks-web.conf.example    nginx reverse-proxy site
├── sync/                              two-host (Pi ingest + VM web) sync layer
│   ├── README.md                      two-host setup + cycle docs
│   ├── fetchlinks-sync.sh             Pi cycle: pull control.db, ingest, retain, snapshot, push data.db
│   ├── fetchlinks-sync.env.example    sync service environment (VM target, paths)
│   └── authorized_keys.example        VM-side rsync-restricted SSH key
└── systemd/
    ├── fetchlinks-web.service         Next.js web app (web role)
    ├── fetchlinks-ingest.service      Python ingest one-shot (single-host)
    ├── fetchlinks-ingest.timer        ingest schedule, every 30 min (single-host)
    ├── fetchlinks-retain.service      weekly DB retention one-shot (single-host)
    ├── fetchlinks-retain.timer        retention schedule, Sun 03:30 (single-host)
    ├── fetchlinks-export-rss-feeds.service  rss_feeds DB → runtime text snapshot (web role)
    ├── fetchlinks-export-rss-feeds.timer    snapshot schedule, every 5 min (web role)
    ├── fetchlinks-sync.service         Pi sync cycle (ingest role)
    └── fetchlinks-sync.timer           Pi sync schedule, every 30 min (ingest role)
```

## Roles (single-host vs two-host)

`bootstrap.sh` provisions one of three roles via `FETCHLINKS_ROLE`:

- **`all`** (default) — single-host: web + ingest + retain + export on one VM,
  one SQLite file. This is the standard deployment described below.
- **`web`** — the Azure VM in a two-host split. Builds/serves the web GUI,
  owns the canonical control.db (feed/subreddit identity), reads a data.db
  replica pushed by the Pi. Set `FETCHLINKS_DB` (data.db replica) and
  `FETCHLINKS_CONTROL_DB` (canonical control.db) in `web/.env.production`.
- **`ingest`** — a home Raspberry Pi. Runs the sync cycle (pull control.db,
  ingest, retain, push data.db); no Node/web build, no inbound web ports.

The two-host split exists to move ingest onto a residential IP (Azure IPs are
throttled by many RSS hosts). Its full setup — VM rsync key lockdown, Pi
config, env — lives in [sync/README.md](sync/README.md). The rest of this
document covers the default single-host (`all`) deployment.

## First-time install

1. Create a fresh Ubuntu 24.04 VM in Azure. Add your SSH public key and open
   ports 22, 80, and 443.

2. SSH in as your admin user, clone the repo, and run bootstrap:

    ```bash
    sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/poptart-sommelier/fetchlinks.git ~/fetchlinks
   sudo ~/fetchlinks/deploy/bootstrap.sh
    ```

   `bootstrap.sh` installs packages, builds the app, installs the systemd
   services, enables the firewall, and starts the app. It does **not** install
   nginx or TLS. Run it from an interactive SSH terminal so you can answer the
   first-install prompts.

3. Answer the first-install prompts.

   For each missing enabled source, bootstrap asks whether to configure it.
   Enter either a path to an existing JSON credential file or paste a JSON
   object directly. Pasted JSON is written to the default path from
   `<checkout>/ingest/data/config/fetchlinks.toml`; copied/pasted secrets are
   owned by `fetchlinks:fetchlinks` and chmod `0600`.

   You can skip credential setup at the first prompt and do it later. Bootstrap
   leaves sources enabled when credentials are skipped, so validation may warn
   until those files exist. See `ingest/SETUP.md` for the JSON formats.

   Bootstrap also configures `<checkout>/web/.env.production` when it is
   missing. The admin password defaults to a generated strong password unless
   you enter your own.

4. Review the validation output.

   Bootstrap seeds the source tables, validates each enabled ingest source, and
   finishes successfully even if one source fails. Failed sources are printed as
   clear warnings so you can fix credentials or API access without reinstalling
   the VM.

5. Optional: copy an existing `fetchlinks.db` to
   `<checkout>/ingest/db/fetchlinks.db`.

6. Optional: point a DNS record at the VM, then install nginx and Let's Encrypt
   TLS:

    ```bash
    sudo FETCHLINKS_DOMAIN=fetchlinks.example.com \
         FETCHLINKS_EMAIL=you@example.com \
          ~/fetchlinks/deploy/tls.sh
    ```

    `tls.sh` is idempotent — re-run it to rotate cert metadata or after
    changing the domain. Renewals happen automatically via `certbot.timer`.

## Updating an existing VM

```bash
sudo ~/fetchlinks/deploy/bootstrap.sh
```

The script is idempotent. It fast-forwards the checkout on `master`, reinstalls
ingest deps, rebuilds the web app, preserves `.env.production`, and restarts
services. If the checkout has local tracked-file changes or diverged history,
the fast-forward fails and the script stops rather than overwriting work.

To deploy a specific tag/branch:

```bash
sudo FETCHLINKS_REPO_REF=v1.2.3 ~/fetchlinks/deploy/bootstrap.sh
```

By default, `bootstrap.sh` uses the checkout that contains the script. The
checkout can live under your admin user's home directory, such as
`/home/azureuser/fetchlinks`. Bootstrap grants the unprivileged `fetchlinks`
service account execute-only ACL access on the checkout's parent directories so
git, systemd, and the app can traverse the known path without making the home
directory listable. To use a different checkout path, set
`FETCHLINKS_APP_DIR=/absolute/path/to/fetchlinks`.

## What `bootstrap.sh` does

In order:

1. Sanity-checks root + Ubuntu.
2. `apt-get install` base packages, Python 3.12 toolchain, Node 24 (NodeSource),
   sqlite3, ufw, unattended-upgrades, and ACL tooling. **No nginx here** —
   that's `tls.sh`.
3. Enables the unattended-upgrades schedule.
4. Creates the `fetchlinks` system user/group and standard directories under
   the checkout (`ingest/db`, `ingest/data/logs`, `ingest/data/config`), then
   grants the service account execute-only ACL access to checkout parent
   directories when needed.
5. Configures `ufw` (deny inbound, allow 22/80/443).
6. Fast-forwards the checkout using
   `git merge --ff-only` (no destructive reset).
7. Prompts for missing enabled-source credentials. Each prompt accepts either a
   readable JSON file path or a pasted JSON object. Skipped credentials leave
   sources enabled and produce warnings later.
8. Prompts for web admin credentials when `<checkout>/web/.env.production`
   is missing. Press Enter at the password prompt to generate a strong random
   password.
9. Builds the Python venv and installs `ingest/requirements.txt`.
10. `npm ci && npm run build` in `web/`.
11. Installs the seven systemd units (web + ingest + retain + export-rss-feeds),
    daemon-reload, enable + start.
12. On first run, seeds the `rss_feeds` SQLite table from
   `<checkout>/ingest/data/config/rss_feeds.txt` (no-op once the table
   has any rows), then exports the DB snapshot to
   `/var/lib/fetchlinks/rss_feeds.txt`.
13. Runs per-source ingest validation and prints non-fatal warnings for sources
   that fail.

## What `tls.sh` does

Decoupled from bootstrap so you can stand the box up before DNS exists and
add TLS later, or re-run only the TLS step when changing domain.

1. Requires `FETCHLINKS_DOMAIN` + `FETCHLINKS_EMAIL` (env or positional args).
2. Bails if `bootstrap.sh` hasn't already run (looks for the nginx site
   template in the repo).
3. `apt-get install nginx python3-certbot-nginx`.
4. Renders `deploy/nginx/fetchlinks-web.conf.example` for the domain,
   enables the site, removes the default nginx welcome site, reloads nginx.
5. Runs `certbot --nginx --redirect` to obtain (or renew) the cert.
6. Enables `certbot.timer` for unattended renewals.

## Rebuild drill

1. Provision a new Ubuntu VM, point DNS at it.
2. SSH in, clone the repo, and run `bootstrap.sh`.
3. `scp` the credential JSON files (and optionally a `fetchlinks.db`
   snapshot) into place. Set the admin user/pass in
   `<checkout>/web/.env.production` and `systemctl restart fetchlinks-web`.
4. Run `tls.sh` with domain + email.
5. Done.

## Day-to-day ops

```bash
systemctl status fetchlinks-web.service
systemctl status fetchlinks-ingest.timer
systemctl status fetchlinks-retain.timer
systemctl status fetchlinks-export-rss-feeds.timer
systemctl list-timers fetchlinks-ingest.timer fetchlinks-retain.timer fetchlinks-export-rss-feeds.timer
journalctl -u fetchlinks-web.service -f
journalctl -u fetchlinks-ingest.service --since '1 hour ago'
journalctl -u fetchlinks-retain.service --since '7 days ago'
journalctl -u fetchlinks-export-rss-feeds.service --since '7 days ago'
```

## Filesystem layout on the VM

```text
~/fetchlinks/                       git checkout, owned by fetchlinks after bootstrap
~/fetchlinks/.venv/                 Python venv for ingest
~/fetchlinks/ingest/data/config/fetchlinks.toml  runtime config
~/fetchlinks/ingest/data/config/rss_feeds.txt    first-install seed file
/var/lib/fetchlinks/rss_feeds.txt                5-minute DB snapshot
~/fetchlinks/ingest/db/fetchlinks.db             SQLite DB
~/fetchlinks/ingest/data/logs/                   ingest logs
~/fetchlinks/web/.env.production                 env vars for the web service
```

All services run as the unprivileged `fetchlinks` system user.
