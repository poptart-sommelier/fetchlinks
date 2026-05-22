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
cd ingest/fetchlinks
python3 fetch_links.py
```

To use a non-default config file, pass `--config /path/to/fetchlinks.toml`.

## Config files

- Runtime config: `ingest/fetchlinks/data/config/fetchlinks.toml`
  (paths, ingest policy, and per-source `enabled` flags + credential paths).
- RSS feed URLs: `ingest/fetchlinks/data/config/rss_feeds.txt`
  (one URL per line; `#` comments and blank lines ignored).

Use the per-source `enabled` flag in `fetchlinks.toml` to toggle providers
without changing code.

## Notes

- Bluesky uses the official atproto SDK.
- Bluesky ingestion persists pagination cursor state in the database and
  resumes on later runs.
- Log output is written to `[paths].log_file` from `fetchlinks.toml`.
