# Fetchlinks two-host sync (Pi ingest + VM web)

The ingest job runs on a home-network Raspberry Pi (residential IP, to escape
Azure-IP RSS throttling); the web GUI runs on the Azure VM. They share state
through two SQLite files, one writer each:

- **control.db** — VM-owned. Feed/subreddit *identity + on/off*. The web admin
  writes it; the Pi only reads a pulled copy.
- **data.db** — Pi-owned. Everything ingest produces (posts, per-feed health,
  follows snapshots, ingest cursors). The Pi writes it; the web only reads it.

Every cycle is **Pi-initiated** over SSH/rsync, so there is no inbound
connection to the home network and no new service on the VM.

## Files

```
deploy/sync/
├── fetchlinks-sync.sh            Pi-side cycle: pull control.db, ingest, retain, snapshot, push data.db
├── fetchlinks-sync.env.example   environment for the sync service (VM target, paths)
└── authorized_keys.example       VM-side locked-down rsync-only SSH key
deploy/systemd/
├── fetchlinks-sync.service       runs fetchlinks-sync.sh (Pi)
└── fetchlinks-sync.timer         every 30 min (Pi)
```

## One cycle (`fetchlinks-sync.sh`)

1. **Pull control.db** from the VM (non-fatal on failure — a one-cycle lag is
   acceptable; ingest continues against the existing local replica).
2. **Ingest** — `fetch_links.py` reads the control replica, writes data.db.
3. **Retention** — `retain.py` prunes old posts. Retention runs **only on the
   Pi**; the VM's data.db is a pure replica.
4. **Snapshot** — `sqlite3 VACUUM INTO` makes a consistent, compacted copy.
5. **Push** — rsync ships the snapshot up; rsync's temp-file-then-rename lands
   it atomically. The web opens data.db per request read-only, so the swap is
   seamless — no web restart.

## VM setup

1. Pick a sync directory the web role reads from, e.g. `/var/lib/fetchlinks/sync`,
   holding `control.db` (canonical) and `data.db` (replica). Point the web
   service env at them:

   ```
   FETCHLINKS_CONTROL_DB=/var/lib/fetchlinks/sync/control.db
   FETCHLINKS_DB=/var/lib/fetchlinks/sync/data.db
   ```

2. Add the Pi's public key to the VM `fetchlinks` account's
   `~/.ssh/authorized_keys`, restricted to rsync within that directory — see
   `authorized_keys.example`.

## Pi setup

1. Provision the checkout (ingest role) and a Python venv (see the role-split
   `bootstrap.sh`).
2. In the ingest config `fetchlinks.toml`, set:

   ```toml
   [paths]
   db = "db/data.db"          # Pi-owned, written by ingest
   control_db = "db/control.db"  # pulled replica, read-only to ingest
   ```

3. Copy `fetchlinks-sync.env.example` to `fetchlinks-sync.env`, set
   `FETCHLINKS_VM_SSH` (and key path if not the default), then enable the timer:

   ```bash
   systemctl enable --now fetchlinks-sync.timer
   ```

4. Trigger one cycle and watch it:

   ```bash
   systemctl start fetchlinks-sync.service
   journalctl -u fetchlinks-sync.service -f
   ```

## Single-host mode (no Pi)

When `control_db` is unset it defaults to `db`, so a single VM keeps using one
physical file and none of this sync machinery runs — the split is opt-in.
