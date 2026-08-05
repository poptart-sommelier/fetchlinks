# Fetchlinks Ingest

The ingest app gathers posts with external links from configured sources.

Current sources:

- RSS feeds
- Reddit subreddits
- Bluesky home timeline
- Mastodon home timelines (multi-instance)

Collection is split from persistence. `fetch_links.py` is the **collector**: it
fetches, normalizes, and deduplicates (by a hash of the extracted URLs), then
writes one batch of records to disk. A destination-specific **publisher** reads
those batches and applies them. The collector never opens a database.

## Quick start

For complete setup steps, see [SETUP.md](SETUP.md).

Once setup is complete, run:

```bash
cd ingest
python3 catalog_tool.py build-from-seeds   # first run only, without a publisher
python3 fetch_links.py
```

To use a non-default config file, pass `--config /path/to/fetchlinks.toml`.

## Config files

- Runtime config: `ingest/data/config/fetchlinks.toml`
  (paths, ingest policy, and per-source `enabled` flags + credential paths).
  `[paths].runtime_dir` optionally moves the collector's runtime directory;
  when omitted it falls back to `FETCHLINKS_RUNTIME_DIR`, then
  `~/.fetchlinks/runtime`.
- RSS feeds: feed identity (URL, `enabled`, `deleted_at`) lives in the
  PostgreSQL `catalog.rss_feeds` table (the live source of truth); per-feed
  health (cache headers, consecutive failures, last error/status) lives in
  `content.rss_feed_health`, keyed by `normalized_url`. The two are joined by
  that natural key rather than a row id, so health survives a feed being
  removed and re-added, and the admin-owned and publisher-owned schemas stay
  independently writable.
  Seed the catalog with `publish_tool.py bootstrap-catalog`; grow it later with
  `rss_feed_import.py` (`--input` / `--pruned`), which validates candidates over
  the network first. A deterministic snapshot of the catalog is written by
  `export_rss_feeds.py` to `[sources.rss].export_path`; in dev this
  intentionally updates `ingest/data/config/rss_feeds.txt`, the seed file you
  review and commit.
- Seed files: `catalog_seed.py` holds the parsing and key normalization for
  `rss_feeds.txt` and `subreddits.txt`. It is shared by the collector-side
  catalog builder and the destination-side bootstrap, so both agree on the
  natural keys (`normalized_url`, `normalized_name`) that join a catalog entry
  to everything observed about it.

Use the per-source `enabled` flag in `fetchlinks.toml` to toggle providers
without changing code.

## The catalog

The collector reads *what to collect* from a single file,
`runtime/catalog/catalog.v1.json`. Feeds and subreddits are managed in the web
admin, so their identity is canonical in the destination database; a publisher
exports this snapshot and the collector reads only the file. That keeps the
collector working from the last good snapshot when the destination is
unreachable — only a machine that has never synced has nothing to fall back on.

The catalog carries identity and nothing else: no health, no counters, no
cursors. Anything the collector can derive itself stays in collector state, so a
stale catalog can never roll back a resume position. Its `revision` is a digest
of the entries rather than an export timestamp, so an unchanged subscription
list keeps the same revision and a batch's `catalog_revision` answers "which
list produced this?".

```bash
cd ingest
python3 catalog_tool.py show                      # current snapshot
python3 catalog_tool.py build-from-seeds          # build one from the seed files
python3 catalog_tool.py build-from-seeds --force  # replace an exported catalog
```

`build-from-seeds` is both the bootstrap for a brand-new install and the way to
exercise collection on a machine that has never talked to a database. It refuses
to overwrite a catalog exported from a database unless `--force` is passed,
because a seed list would silently resurrect feeds the admin removed.

## Collection pipeline (`pipeline/`)

`pipeline/` is the boundary between *collecting* data and *storing* it. It is
deliberately free of any database code, so the collecting half of the system can
run anywhere while the storing half stays specific to one destination.

- `pipeline/contract.py` — contract v1: the normalized, destination-neutral
  record types (posts, RSS observations, source checkpoints, follows
  snapshots) plus deterministic JSON/NDJSON serialization.
- `pipeline/schemas/*.json` — the checked-in JSON Schemas. These are
  normative: validation runs the real schemas rather than a Python
  re-implementation, so the documented and enforced contracts cannot drift.
- `pipeline/spool.py` — the crash-safe batch queue.
- `pipeline/state.py` — the collector's private resume cursors and RSS cache
  headers.
- `pipeline/catalog.py` — the file-backed catalog snapshot.
- `pipeline/collection.py` — what one collection cycle produced, before
  anything is written down. Source modules return one of these and the
  collector merges them into a single batch, which is what makes a cycle
  atomic: a crash mid-Mastodon does not leave Reddit's posts queued and its
  checkpoint lost.
- `pipeline/layout.py` — resolves the runtime directory (`[paths].runtime_dir`,
  then `FETCHLINKS_RUNTIME_DIR`, default `~/.fetchlinks/runtime`).

### Runtime directory

Gitignored, so deploying or rolling back code never disturbs queued batches or
resume state. On the Pi it sits at `~/fetchlinks/runtime/`, inside the checkout
but invisible to git, which is what keeps the whole deployment in one directory
while still making `git pull` safe:

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
- Every source resumes from `runtime/state/collector-state.v1.json`: Bluesky and
  Mastodon pagination cursors, the newest Reddit fullname per subreddit, and RSS
  `ETag`/`Last-Modified` cache validators. Nothing about resuming depends on the
  destination being reachable.
- Follows snapshots distinguish *absent* from *empty*. A failed sync reports no
  snapshot at all, so the publisher leaves the stored list alone; an empty
  snapshot is a real observation that the account follows nobody, and clears it.
- Log output is written to `[paths].log_file` from `fetchlinks.toml`.
- `publish_tool.py retain` prunes posts older than
  `[retention].max_post_age_months` (falling back to
  `[ingest].max_post_age_months`) and forgets batch-ledger rows that can no
  longer be replayed. It issues no `VACUUM`: PostgreSQL autovacuum handles
  reclamation, and a manual full vacuum would take a lock the web app cannot
  afford. In production it runs weekly via `fetchlinks-retain.timer`.

## The publisher (`publisher/`)

The destination-specific half. It is the only code that holds a database URL,
and the collector cannot import it without failing `test_collector_boundary.py`.

```bash
cd ingest
export FETCHLINKS_DATABASE_URL='postgresql://...'
python3 publish_tool.py migrate             # apply db/migrations, owner role
python3 publish_tool.py bootstrap-catalog   # first run only, from the seed files
python3 publish_tool.py sync-catalog        # export catalog.v1.json for the collector
python3 publish_tool.py publish             # drain every ready batch, FIFO
python3 publish_tool.py retain              # apply the post age limit
python3 publish_tool.py status              # queue depth and database totals
```

See [../db/README.md](../db/README.md) for the schema, the migration rules, and
the three database roles.

Two failure distinctions in `publisher/drain.py` are worth knowing, because
they are what keep collected data from being lost:

- A batch that fails **validation** is permanently unusable, so it is moved to
  `failed` and the drain continues.
- A batch that fails on a **database error** is left in `processing` and the
  drain stops. Continuing would spin on a destination that is down, and the
  next run retries `processing` first.
