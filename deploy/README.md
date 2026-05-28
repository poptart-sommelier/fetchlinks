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
└── systemd/
    ├── fetchlinks-web.service         Next.js web app
    ├── fetchlinks-ingest.service      Python ingest one-shot
    ├── fetchlinks-ingest.timer        ingest schedule (every 30 min)
    ├── fetchlinks-retain.service      weekly DB retention one-shot
    ├── fetchlinks-retain.timer        retention schedule (Sun 03:30)
    ├── fetchlinks-export-rss-feeds.service  rss_feeds DB → text seed snapshot
    └── fetchlinks-export-rss-feeds.timer    snapshot schedule (every 5 min)
```

## First-time install

1. Create a fresh Ubuntu 24.04 VM in Azure. Add your SSH public key, open
   ports 22/80/443.
2. SSH in as your admin user.
3. Run:

    ```bash
    sudo apt-get update && sudo apt-get install -y git
    sudo git clone https://github.com/poptart-sommelier/fetchlinks.git /opt/fetchlinks
    sudo /opt/fetchlinks/deploy/bootstrap.sh
    ```

    `bootstrap.sh` installs the app, services, and firewall rules. It does
    **not** touch nginx or TLS — see step 7 for that.

4. Ensure the API credential files referenced by
   `/opt/fetchlinks/ingest/data/config/fetchlinks.toml` exist. The bootstrap
   script does not create, copy, chmod, or otherwise manage credentials.

   Then edit `/opt/fetchlinks/web/.env.production` and set the admin Basic
   auth credentials:

    ```bash
    sudo editor /opt/fetchlinks/web/.env.production
    sudo systemctl restart fetchlinks-web.service
    ```

    Then (optionally) seed or extend the RSS feed list. The DB table
    `rss_feeds` is the live source of truth;
    `/opt/fetchlinks/ingest/data/config/rss_feeds.txt` is the first-install
    seed and the 5-minute snapshot exported back from the DB:

    ```bash
    # First-time seed (no-op once the rss_feeds table has any rows):
    sudo -u fetchlinks /opt/fetchlinks/.venv/bin/python \
        /opt/fetchlinks/ingest/rss_feed_import.py \
        --config /opt/fetchlinks/ingest/data/config/fetchlinks.toml \
        --seed-if-empty /opt/fetchlinks/ingest/data/config/rss_feeds.txt

    # Vet and add new feeds from an arbitrary text blob:
    sudo -u fetchlinks /opt/fetchlinks/.venv/bin/python \
        /opt/fetchlinks/ingest/rss_feed_import.py \
        --config /opt/fetchlinks/ingest/data/config/fetchlinks.toml \
        --input /tmp/new-feeds.txt
    ```

    A short timer (`fetchlinks-export-rss-feeds.timer`, every 5 minutes)
    writes a deterministic text snapshot of the live table back to
    `/opt/fetchlinks/ingest/data/config/rss_feeds.txt` for backup / diffing
    and occasional commit back to the repo seed file.

5. (Optional) copy an existing `fetchlinks.db` to
   `/opt/fetchlinks/ingest/db/fetchlinks.db`.
6. Kick off an ingest run to verify:

    ```bash
    sudo systemctl start fetchlinks-ingest.service
    sudo journalctl -u fetchlinks-ingest.service -n 50 --no-pager
    ```

7. (Optional) point a DNS record at the VM, then provision nginx + Let's
   Encrypt TLS with the dedicated script:

    ```bash
    sudo FETCHLINKS_DOMAIN=fetchlinks.example.com \
         FETCHLINKS_EMAIL=you@example.com \
         /opt/fetchlinks/deploy/tls.sh
    ```

    `tls.sh` is idempotent — re-run it to rotate cert metadata or after
    changing the domain. Renewals happen automatically via `certbot.timer`.

## Updating an existing VM

```bash
sudo /opt/fetchlinks/deploy/bootstrap.sh
```

The script is idempotent. It fast-forwards the checkout on `master`, reinstalls
ingest deps, rebuilds the web app, preserves `.env.production`, and restarts
services. If the checkout has local tracked-file changes or diverged history,
the fast-forward fails and the script stops rather than overwriting work.

To deploy a specific tag/branch:

```bash
sudo FETCHLINKS_REPO_REF=v1.2.3 /opt/fetchlinks/deploy/bootstrap.sh
```

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

```
/opt/fetchlinks/                       git checkout, owned by fetchlinks
/opt/fetchlinks/.venv/                 Python venv for ingest
/opt/fetchlinks/ingest/data/config/fetchlinks.toml  runtime config
/opt/fetchlinks/ingest/data/config/rss_feeds.txt    first-install seed, then 5-minute DB snapshot
/opt/fetchlinks/ingest/db/fetchlinks.db             SQLite DB
/opt/fetchlinks/ingest/data/logs/                   ingest logs
/opt/fetchlinks/web/.env.production                 env vars for the web service
```

All services run as the unprivileged `fetchlinks` system user.
