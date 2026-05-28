# Fetchlinks VM deployment

This directory contains everything needed to stand up Fetchlinks on a single
Ubuntu 24.04 VM.

## Files

```
deploy/
├── bootstrap.sh                       one-shot installer / updater (run on VM)
├── tls.sh                             nginx + Let's Encrypt provisioner (run after bootstrap)
├── config/
│   ├── fetchlinks.toml                production ingest config (paths, ingest, sources)
│   └── rss_feeds.txt                  seed RSS feed list (one URL per line, used only on first install)
├── nginx/
│   └── fetchlinks-web.conf.example    nginx reverse-proxy site
└── systemd/
    ├── fetchlinks-web.service         Next.js web app
    ├── fetchlinks-web.env.example     env file template for the web service
    ├── fetchlinks-ingest.service      Python ingest one-shot
    ├── fetchlinks-ingest.timer        ingest schedule (every 30 min)
    ├── fetchlinks-retain.service      weekly DB retention one-shot
    ├── fetchlinks-retain.timer        retention schedule (Sun 03:30)
    ├── fetchlinks-export-rss-feeds.service  daily rss_feeds DB → text snapshot
    └── fetchlinks-export-rss-feeds.timer    snapshot schedule (daily)
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

4. Drop your API credential files into `/etc/fetchlinks/credentials/` and
   enable the matching sources in `/etc/fetchlinks/fetchlinks.toml`:

    ```bash
    # One file per credentialed source, names match `credential_location` in fetchlinks.toml.
    scp reddit.json bluesky.json mastodon-infosec.json deploy@<vm>:/tmp/
    ssh deploy@<vm> 'sudo install -d -o root -g fetchlinks -m 0750 /etc/fetchlinks/credentials \
        && sudo install -o root -g fetchlinks -m 0640 /tmp/reddit.json   /etc/fetchlinks/credentials/ \
        && sudo install -o root -g fetchlinks -m 0640 /tmp/bluesky.json  /etc/fetchlinks/credentials/ \
        && sudo install -o root -g fetchlinks -m 0640 /tmp/mastodon-infosec.json /etc/fetchlinks/credentials/'
    sudo -e /etc/fetchlinks/fetchlinks.toml      # flip `enabled = true` for each source
    ```

    Then (optionally) seed or extend the RSS feed list. The DB table
    `rss_feeds` is the source of truth; `/etc/fetchlinks/rss_feeds.txt` is
    only consulted on first install (when the table is empty) or as input
    to the importer:

    ```bash
    # First-time seed (no-op once the rss_feeds table has any rows):
    sudo -u fetchlinks /opt/fetchlinks/.venv/bin/python \
        /opt/fetchlinks/ingest/rss_feed_import.py \
        --config /etc/fetchlinks/fetchlinks.toml \
        --seed-if-empty /etc/fetchlinks/rss_feeds.txt

    # Vet and add new feeds from an arbitrary text blob:
    sudo -u fetchlinks /opt/fetchlinks/.venv/bin/python \
        /opt/fetchlinks/ingest/rss_feed_import.py \
        --config /etc/fetchlinks/fetchlinks.toml \
        --input /tmp/new-feeds.txt
    ```

    A daily timer (`fetchlinks-export-rss-feeds.timer`) writes a
    deterministic text snapshot of the live table to
    `/var/lib/fetchlinks/rss_feeds.txt` for backup / diffing.

5. (Optional) copy an existing `fetchlinks.db` to `/var/lib/fetchlinks/`.
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

The script is idempotent. It pulls the latest commit on `master`, reinstalls
ingest deps, rebuilds the web app, re-renders config, restarts services.

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
/var/lib/fetchlinks/fetchlinks.db      SQLite DB (mode 0640 fetchlinks:fetchlinks)
/var/lib/fetchlinks/rss_feeds.txt      Daily exported snapshot of the rss_feeds table
/var/log/fetchlinks/                   ingest logs
/etc/fetchlinks/fetchlinks.toml        non-secret config (mode 0640 root:fetchlinks)
/etc/fetchlinks/rss_feeds.txt          Seed feed list (read only when rss_feeds is empty)
/etc/fetchlinks/credentials/           per-source API credential JSON files
/etc/fetchlinks/web.env                env vars for the web service
/etc/fetchlinks/ingest.env             env vars for the ingest service (optional)
```

All services run as the unprivileged `fetchlinks` system user.
