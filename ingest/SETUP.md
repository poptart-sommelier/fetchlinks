# Setup

These instructions configure and run the Fetchlinks ingest app on Linux.

## 1) Create and activate virtual environment

From the monorepo root:

```bash
python3 -m venv ../venv
source ../venv/bin/activate
cd ingest
```

## 2) Install dependencies

```bash
pip install -r requirements.txt
```

## 3) Configure credentials

Create a credential directory:

```bash
mkdir -p ~/.fetchlinks
chmod 700 ~/.fetchlinks
```

### Reddit credential file

Create ~/.fetchlinks/reddit.json:

```json
{
	"reddit": {
		"APP_CLIENT_ID": "your_client_id",
		"APP_CLIENT_SECRET": "your_client_secret",
		"USERNAME": "your_reddit_username"
	}
}
```

The `USERNAME` field is recommended — Reddit's API rules ask for a unique User-Agent of the form `<platform>:<app>:<version> (by /u/<username>)`. Without it, requests are more likely to be rate-limited.

Restrict permissions so only your user can read it:

```bash
chmod 600 ~/.fetchlinks/reddit.json
```

### Bluesky credential file (optional)

Bluesky is disabled by default. If you want to enable it, create ~/.fetchlinks/bluesky.json:

```json
{
	"bluesky": {
		"IDENTIFIER": "your-handle.bsky.social",
		"APP_PASSWORD": "xxxx-xxxx-xxxx-xxxx"
	}
}
```

Restrict permissions:

```bash
chmod 600 ~/.fetchlinks/bluesky.json
```

### Mastodon credential files (optional)

Mastodon is disabled by default. Each Mastodon instance/account gets its own
credential file so multiple instances can be configured independently. For
example, create ~/.fetchlinks/mastodon-infosec.json:

```json
{
	"mastodon": {
		"ACCESS_TOKEN": "your_read_only_access_token"
	}
}
```

Restrict permissions:

```bash
chmod 600 ~/.fetchlinks/mastodon-infosec.json
```

## 4) Configure runtime

All non-secret runtime configuration lives in a single TOML file:
`ingest/fetchlinks/data/config/fetchlinks.toml`. Path values may be absolute
or relative to the TOML file's directory. The schema is:

- `[paths]` — `db`, `log_file`, `log_level` (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`).
- `[ingest]` — `max_post_age_months`, `excluded_url_host_keywords`,
  `excluded_url_or_description_keywords`.
- `[retention]` — `enabled` (default `true`), optional `max_post_age_months`
  (falls back to `[ingest].max_post_age_months`), `vacuum_threshold_pages`
  (default `1000`). Drives the weekly retention job (`retain.py`).
- `[sources.rss]` — `enabled`, `feeds_file` (path to a plain-text feed list).
- `[sources.reddit]` — `enabled`, `credential_location`, `subreddits`,
  optional `listing_limit` (default 100) and `max_pages` (default 5).
- `[sources.bluesky]` — `enabled`, `credential_location`, `timeline_limit`.
- `[sources.mastodon]` — `enabled`, then one `[[sources.mastodon.instances]]`
  block per account with `name`, `instance_url` (must be `https://`),
  `credential_location`, `timeline` (must be `home`), `timeline_limit`.

Notes:

- Each `credential_location` must point at an existing readable JSON file
  (paths starting with `~` are expanded).
- `excluded_url_host_keywords` are case-insensitive substring matches against
  the URL hostname only. `"insider"` blocks `www.businessinsider.com`;
  `"businessinsider.com"` blocks that domain and its subdomains.
- `excluded_url_or_description_keywords` are case-insensitive: URL matches are
  substrings, description matches are whole-word. `"politics"` blocks URLs
  containing `/politics/` and descriptions containing the word `politics`.

### RSS feeds file

RSS feed URLs live in a separate plain-text file referenced by
`[sources.rss].feeds_file` (default `rss_feeds.txt` next to the TOML). One
URL per line; blank lines and lines beginning with `#` are ignored:

```text
# infosec
https://example.com/feed.xml
https://blog.example/rss
```

To bulk-import a list of candidate feeds, the importer validates each one,
drops feeds with no posts in the last 365 days, and appends survivors to the
configured feeds file (writing a `.bak` of the previous contents):

```bash
cd fetchlinks
python3 rss_feed_import.py --input /tmp/rss-list.txt
```

To review first, use dry-run mode. It writes accepted feeds to
`/tmp/rss-list.txt.pruned` without editing the feeds file:

```bash
python3 rss_feed_import.py --input /tmp/rss-list.txt --dry-run
python3 rss_feed_import.py --pruned /tmp/rss-list.txt.pruned
```

Use `--abandoned-days N` to change the cutoff for rejecting feeds with no
recent posts. Use `--feeds-file /path/to/rss_feeds.txt` to target a feeds
file other than the default.

## 5) Run the backend

```bash
cd fetchlinks
python3 fetch_links.py
```

The default config file is `data/config/fetchlinks.toml`. To use a different
file, pass `--config /path/to/fetchlinks.toml`.

On first run, the backend initializes the SQLite database automatically if it
does not exist.

## 6) Validate output

- Database location is controlled by `[paths].db` in `fetchlinks.toml`.
- Logs are written to `[paths].log_file` at `[paths].log_level`.
