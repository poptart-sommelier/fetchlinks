# Fetchlinks VM deployment

This directory contains everything needed to stand up Fetchlinks on a single
Ubuntu 24.04 VM.

## Files

```
deploy/
├── bootstrap.sh                       one-shot installer / updater (run on VM)
├── config/
│   ├── fetchlinks.toml                production ingest config (paths, ingest, sources)
│   └── rss_feeds.txt                  seed RSS feed list (one URL per line)
├── nginx/
│   └── fetchlinks-web.conf.example    nginx reverse-proxy site
└── systemd/
    ├── fetchlinks-web.service         Next.js web app
    ├── fetchlinks-web.env.example     env file template for the web service
    ├── fetchlinks-ingest.service      Python ingest one-shot
    ├── fetchlinks-ingest.timer        ingest schedule (every 30 min)
    ├── fetchlinks-retain.service      weekly DB retention one-shot
    └── fetchlinks-retain.timer        retention schedule (Sun 03:30)
```

## First-time install

1. Create a fresh Ubuntu 24.04 VM in Azure. Add your SSH public key, open
   ports 22/80/443.
2. SSH in as your admin user.
3. Run:

    ```bash
    sudo apt-get update && sudo apt-get install -y git
    sudo git clone https://github.com/poptart-sommelier/fetchlinks.git /opt/fetchlinks
    sudo FETCHLINKS_DOMAIN=fetchlinks.example.com \
         FETCHLINKS_EMAIL=you@example.com \
         /opt/fetchlinks/deploy/bootstrap.sh
    ```

    Omit `FETCHLINKS_DOMAIN`/`FETCHLINKS_EMAIL` if you don't want nginx + TLS
    on this run; you can re-run the script later with them set.

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

    Then (optionally) seed the RSS feed list:

    ```bash
    sudo -u fetchlinks /opt/fetchlinks/.venv/bin/python \
        /opt/fetchlinks/ingest/fetchlinks/rss_feed_import.py \
        --input /tmp/new-feeds.txt \
        --feeds-file /etc/fetchlinks/rss_feeds.txt
    ```

5. (Optional) copy an existing `fetchlinks.db` to `/var/lib/fetchlinks/`.
6. Kick off an ingest run to verify:

    ```bash
    sudo systemctl start fetchlinks-ingest.service
    sudo journalctl -u fetchlinks-ingest.service -n 50 --no-pager
    ```

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
systemctl list-timers fetchlinks-ingest.timer fetchlinks-retain.timer
journalctl -u fetchlinks-web.service -f
journalctl -u fetchlinks-ingest.service --since '1 hour ago'
journalctl -u fetchlinks-retain.service --since '7 days ago'
```

## Filesystem layout on the VM

```
/opt/fetchlinks/                       git checkout, owned by fetchlinks
/opt/fetchlinks/.venv/                 Python venv for ingest
/var/lib/fetchlinks/fetchlinks.db      SQLite DB (mode 0640 fetchlinks:fetchlinks)
/var/log/fetchlinks/                   ingest logs
/etc/fetchlinks/fetchlinks.toml        non-secret config (mode 0640 root:fetchlinks)
/etc/fetchlinks/rss_feeds.txt          RSS feed URLs (mode 0640 root:fetchlinks)
/etc/fetchlinks/credentials/           per-source API credential JSON files
/etc/fetchlinks/web.env                env vars for the web service
/etc/fetchlinks/ingest.env             env vars for the ingest service (optional)
```

All services run as the unprivileged `fetchlinks` system user.
