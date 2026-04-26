# fetchlinks backend

fetchlinks ingests posts with external links from configured sources and stores them in a local SQLite database.

Current sources:
- RSS feeds
- Reddit subreddits
- Bluesky home timeline (optional, disabled by default)

The backend deduplicates rows using a hash of extracted URLs and stores results in the posts table.

## Quick start

For complete setup steps, see SETUP.md.

Once setup is complete, run:

```bash
cd fetchlinks/fetchlinks
python3 fetch_links.py
```

To use non-default config files, pass `--config` and `--sources`.

## Config files

- App config: fetchlinks/data/config/config.json
- Source config: fetchlinks/data/config/sources.json

Use source-level enabled flags to toggle providers without changing code.

## Notes

- Bluesky uses the official atproto SDK.
- Bluesky ingestion persists pagination cursor state in the database and resumes on later runs.
- Log output is written to the path configured in config.json.