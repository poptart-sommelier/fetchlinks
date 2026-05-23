# Setup

These instructions configure and run the Fetchlinks ingest app on Linux.

## 1) Create and activate virtual environment

From the monorepo root. The venv MUST live at `.venv` in the repo root
(not a sibling `../venv`): VS Code (`.vscode/settings.json`,
`.vscode/tasks.json`) and the production install at `/opt/fetchlinks/.venv`
both assume this path. `.venv/` is already in `.gitignore`.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 2) Install dependencies

```bash
pip install -r ingest/requirements.txt
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
- `[sources.rss]` — `enabled`, optional `seed_file` (read only when the
  `rss_feeds` table is empty), optional `export_path` (where
  `export_rss_feeds.py` writes its snapshot), `auto_disable_after_failures`
  (default `10`; set `0` to disable), `request_timeout_seconds`
  (default `10`).
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

### RSS feeds

The `rss_feeds` SQLite table is the source of truth for which feeds get
polled. Each row tracks the URL, an `enabled` flag, a `deleted_at`
tombstone, and per-feed health (etag / last-modified cache headers,
consecutive failure count, last error). The ingest job auto-disables a
feed after `auto_disable_after_failures` consecutive failures.

Three workflows feed rows into the table via `rss_feed_import.py`:

```bash
cd fetchlinks
# First-time bulk seed from a plain-text file (no-op once the table has rows):
python3 rss_feed_import.py --seed-if-empty data/config/rss_feeds.txt

# Validate candidate URLs over the network, then INSERT OR IGNORE survivors:
python3 rss_feed_import.py --input /tmp/rss-list.txt

# Same but dry-run first; produces /tmp/rss-list.txt.pruned for review.
python3 rss_feed_import.py --input /tmp/rss-list.txt --dry-run
python3 rss_feed_import.py --pruned /tmp/rss-list.txt.pruned
```

Use `--abandoned-days N` to change the cutoff for rejecting feeds with no
recent posts.

A daily snapshot of the table is written by `export_rss_feeds.py` to
`[sources.rss].export_path` (three sections: active feeds, commented
disabled feeds with their failure reason, commented tombstoned feeds).
The snapshot is for backup/diffing only — do not hand-edit it.

## 5) Seed the rss_feeds table (first run only)

`fetch_links.py` does **not** read `rss_feeds.txt` directly — RSS feeds live
in the `rss_feeds` SQLite table, and `[sources.rss].seed_file` in the TOML
is only consumed by `rss_feed_import.py --seed-if-empty`. If you skip this
step, ingest will still run, but RSS will contribute zero posts because the
table is empty. On production this is run once by `deploy/bootstrap.sh`; in
dev you do it by hand:

```bash
cd fetchlinks
python3 rss_feed_import.py --seed-if-empty data/config/rss_feeds.txt
```

The command is a no-op once the table has any rows, so it's safe to re-run.
See section 4's "RSS feeds" subsection for the other `rss_feed_import.py`
workflows (validated add / dry-run / pruned import).

## 6) Run the backend

```bash
cd fetchlinks
python3 fetch_links.py
```

The default config file is `data/config/fetchlinks.toml`. To use a different
file, pass `--config /path/to/fetchlinks.toml`.

On first run, the backend initializes the SQLite database automatically if it
does not exist.

## 7) Validate output

- Database location is controlled by `[paths].db` in `fetchlinks.toml`.
- Logs are written to `[paths].log_file` at `[paths].log_level`.
