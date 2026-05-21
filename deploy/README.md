# Fetchlinks VM deployment

This directory contains everything needed to stand up Fetchlinks on a single
Ubuntu 24.04 VM.

## Files

```
deploy/
├── bootstrap.sh                       one-shot installer / updater (run on VM)
├── config/
│   └── config.json                    production ingest config (paths only)
├── nginx/
│   └── fetchlinks-web.conf.example    nginx reverse-proxy site
└── systemd/
    ├── fetchlinks-web.service         Next.js web app
    ├── fetchlinks-web.env.example     env file template for the web service
    ├── fetchlinks-ingest.service      Python ingest one-shot
    └── fetchlinks-ingest.timer        ingest schedule (every 30 min)
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

4. Copy your real `sources.json` (with API credentials) to the VM:

    ```bash
    scp sources.json deploy@<vm>:/tmp/
    ssh deploy@<vm> 'sudo install -o root -g fetchlinks -m 640 /tmp/sources.json /etc/fetchlinks/sources.json'
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
systemctl list-timers fetchlinks-ingest.timer
journalctl -u fetchlinks-web.service -f
journalctl -u fetchlinks-ingest.service --since '1 hour ago'
```

## Filesystem layout on the VM

```
/opt/fetchlinks/                       git checkout, owned by fetchlinks
/opt/fetchlinks/.venv/                 Python venv for ingest
/var/lib/fetchlinks/fetchlinks.db      SQLite DB (mode 0640 fetchlinks:fetchlinks)
/var/log/fetchlinks/                   ingest logs
/etc/fetchlinks/config.json            non-secret config (mode 0640 root:fetchlinks)
/etc/fetchlinks/sources.json           secrets — bring your own (same perms)
/etc/fetchlinks/web.env                env vars for the web service
/etc/fetchlinks/ingest.env             env vars for the ingest service (optional)
```

All services run as the unprivileged `fetchlinks` system user.
