# Fetchlinks Ingest

The ingest app gathers posts with external links from configured sources and
stores them in a local SQLite database.

Current sources:

- RSS feeds
- Reddit subreddits
- Bluesky home timeline (optional, disabled by default)
- Mastodon home timelines (optional, disabled by default; multi-instance)

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
- RSS feeds: stored in the SQLite `rss_feeds` table (the source of truth).
  Manage with `rss_feed_import.py` (`--input` / `--pruned` / `--seed-if-empty`).
  A snapshot of the table is written daily by `export_rss_feeds.py` to
  `[sources.rss].export_path`. The `seed_file` referenced in `[sources.rss]`
  is read **only** when the `rss_feeds` table is empty (first install).

Use the per-source `enabled` flag in `fetchlinks.toml` to toggle providers
without changing code.

## Notes

- Bluesky uses the official atproto SDK.
- Bluesky ingestion persists pagination cursor state in the database and
  resumes on later runs.
- Log output is written to `[paths].log_file` from `fetchlinks.toml`.
- A separate one-shot, `retain.py`, prunes posts older than
  `[retention].max_post_age_months` (falling back to
  `[ingest].max_post_age_months`) and VACUUMs when enough pages are freed.
  In production it runs weekly via `fetchlinks-retain.timer`.
