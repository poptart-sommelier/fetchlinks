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
- RSS feeds: stored in the SQLite `rss_feeds` table (the live source of truth).
  Manage with `rss_feed_import.py` (`--input` / `--pruned` / `--seed-if-empty`).
  A deterministic snapshot of the table is written by `export_rss_feeds.py` to
  `[sources.rss].export_path`; in dev this intentionally updates
  `ingest/data/config/rss_feeds.txt`, the seed file you review and commit.

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
