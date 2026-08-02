# Fetchlinks Ingest

The ingest app gathers posts with external links from configured sources and
stores them in a local SQLite database.

Current sources:

- RSS feeds
- Reddit subreddits
- Bluesky home timeline
- Mastodon home timelines (multi-instance)

The backend deduplicates rows using a hash of extracted URLs and stores
results in the `posts` table.

## Quick start

For complete setup steps, see [SETUP.md](SETUP.md).

Once setup is complete, run:

```bash
cd ingest
python3 fetch_links.py
```

To use a non-default config file, pass `--config /path/to/fetchlinks.toml`.

## Config files

- Runtime config: `ingest/data/config/fetchlinks.toml`
  (paths, ingest policy, and per-source `enabled` flags + credential paths).
- RSS feeds: feed identity (URL, `enabled`, `deleted_at`) lives in the SQLite
  `rss_feeds` table (the live source of truth); per-feed health (cache headers,
  consecutive failures, last error/status) lives in a separate
  `rss_feed_health` table keyed by `normalized_url`. In single-host mode both
  share one file; the two-host split keeps identity in the control DB and
  health in the data DB (`[paths].control_db`, defaults to `db`).
  Manage feeds with `rss_feed_import.py` (`--input` / `--pruned` / `--seed-if-empty`).
  A deterministic snapshot of the table is written by `export_rss_feeds.py` to
  `[sources.rss].export_path`; in dev this intentionally updates
  `ingest/data/config/rss_feeds.txt`, the seed file you review and commit.

Use the per-source `enabled` flag in `fetchlinks.toml` to toggle providers
without changing code.

## Collection pipeline (`pipeline/`)

`pipeline/` is the boundary between *collecting* data and *storing* it, added
as the first step of the move to PostgreSQL. It is deliberately free of any
database code, so the collecting half of the system can run anywhere while the
storing half stays specific to one destination.

- `pipeline/contract.py` — contract v1: the normalized, destination-neutral
  record types (posts, RSS observations, source checkpoints, follows
  snapshots) plus deterministic JSON/NDJSON serialization.
- `pipeline/schemas/*.json` — the checked-in JSON Schemas. These are
  normative: validation runs the real schemas rather than a Python
  re-implementation, so the documented and enforced contracts cannot drift.
- `pipeline/spool.py` — the crash-safe batch queue.
- `pipeline/state.py` — the collector's private resume cursors and RSS cache
  headers.
- `pipeline/layout.py` — resolves the runtime directory (override with
  `FETCHLINKS_RUNTIME_DIR`, default `~/.fetchlinks/runtime`).

### Runtime directory

Kept outside the checkout so deploying or rolling back code never disturbs
queued batches or resume state:

```text
runtime/
  catalog/catalog.v1.json           what to collect
  state/collector-state.v1.json     where the collector got to
  outbox/{staging,ready,processing,published,failed}/<batch-id>/
      manifest.json
      posts.ndjson
      rss-observations.ndjson
      checkpoints.ndjson
      bluesky-follows.ndjson            optional complete snapshot
      mastodon-follows-<instance>.ndjson optional complete snapshot
```

A batch only ever moves between those directories by rename, which the kernel
performs atomically. That is what lets the collector keep working while the
destination is unreachable, and what lets a publisher crash at any point
without losing or double-applying a batch:

1. The collector builds a batch in `staging` and renames it to `ready`.
2. It advances its own state only once the batch is safely in `ready`. A crash
   before that re-fetches some posts; it never drops any.
3. A publisher claims the oldest `ready` batch by renaming it to `processing`.
4. It applies the whole batch in one transaction, recording the batch id at the
   destination, then renames the batch to `published`.
5. A batch left in `processing` is retried first on the next run. The recorded
   batch id is what stops a replay applying anything twice.

Transient failures leave a batch in `processing` for retry. Only permanently
unusable batches move to `failed`, and those are never auto-deleted.

`manifest.json` names every file in the batch with its record count and SHA-256
hash. A publisher validates all of it — schemas, counts, and checksums — before
touching the destination.

### Inspecting the spool

```bash
cd ingest
python3 spool_tool.py status              # queue depth, oldest waiting batch, disk usage
python3 spool_tool.py list ready
python3 spool_tool.py show <batch-id>     # manifest plus a record preview
python3 spool_tool.py verify <batch-id>   # full schema/count/checksum validation
python3 spool_tool.py demo                # synthetic batch through the whole lifecycle
```

## Notes

- Bluesky uses the official atproto SDK.
- Bluesky ingestion persists pagination cursor state in the database and
  resumes on later runs.
- Log output is written to `[paths].log_file` from `fetchlinks.toml`.
- A separate one-shot, `retain.py`, prunes posts older than
  `[retention].max_post_age_months` (falling back to
  `[ingest].max_post_age_months`) and VACUUMs when enough pages are freed.
  In production it runs weekly via `fetchlinks-retain.timer`.
