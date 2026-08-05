# Setup

These instructions configure and run the Fetchlinks ingest app from a checkout —
a development machine, or any box where you are running the collector by hand.

**They are not the instructions for the Raspberry Pi.** The deployment keeps
everything in one directory under `~/fetchlinks/`, installs its own config from
a template, and reads credentials from `~/fetchlinks/runtime/config/` rather
than from the paths below. See [../deploy/README.md](../deploy/README.md) for
that, and use `deploy/bootstrap.sh` rather than following this file. The two
differ only in *where* the files live; the JSON shapes documented here are the
same on both.

## 1) Create and activate virtual environment

From the monorepo root. The venv MUST live at `.venv` in the repo root
(not a sibling `../venv`): VS Code (`.vscode/settings.json`,
`.vscode/tasks.json`) and the production bootstrap both assume this path.
`.venv/` is already in `.gitignore`.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 2) Install dependencies

```bash
pip install -r ingest/requirements.txt
```

## 3) Configure credentials

Credentials are JSON, one file per source, readable only by you. On a checkout
they live in `~/.fetchlinks/`; **on the Pi the same files live in
`~/fetchlinks/runtime/config/`** alongside `fetchlinks.toml`, because the
deployment is one self-contained directory. Only the directory differs — the
file names and JSON shapes below are identical on both.

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

### Bluesky credential file

Create ~/.fetchlinks/bluesky.json:

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

### Mastodon credential files

Each Mastodon instance/account gets its own credential file so multiple
instances can be configured independently. For example, create
~/.fetchlinks/mastodon-infosec.json:

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
`ingest/data/config/fetchlinks.toml`. Path values may be absolute
or relative to the TOML file's directory. The schema is:

- `[paths]` — `log_file`, `log_level` (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`),
  optional `runtime_dir` (the collector's catalog, resume state, and batch
  spool; falls back to `FETCHLINKS_RUNTIME_DIR`, then `~/.fetchlinks/runtime`).
  The Pi sets `runtime_dir` explicitly so it never relies on that fallback.
  There is deliberately no database setting here: the database URL reaches the
  publisher through `FETCHLINKS_DATABASE_URL`, and the collector never sees one.
- `[ingest]` — `max_post_age_months`, `excluded_url_host_keywords`,
  `excluded_url_or_description_keywords`.
- `[retention]` — `enabled` (default `true`), optional `max_post_age_months`
  (falls back to `[ingest].max_post_age_months`). Drives the weekly retention
  job (`publish_tool.py retain`).
- `[sources.rss]` — `enabled`, optional `seed_file` (read only by
  `publish_tool.py bootstrap-catalog`), optional `export_path` (where
  `export_rss_feeds.py` writes the catalog snapshot; in dev this is the seed
  file), `request_timeout_seconds` (default `10`). The
  `auto_disable_after_failures` field is still accepted (default `10`) but no
  longer disables feeds — see the "RSS feeds" subsection below.
- `[sources.reddit]` — `enabled`, `credential_location`, `subreddits`,
  optional `listing_limit` (default 100) and `max_pages` (default 5).
- `[sources.bluesky]` — `enabled`, `credential_location`, `timeline_limit`.
- `[sources.mastodon]` — `enabled`, then one `[[sources.mastodon.instances]]`
  block per account with `name`, `instance_url` (must be `https://`),
  `credential_location`, `timeline` (must be `home`), `timeline_limit`.

Notes:

- Each `credential_location` must point at an existing readable JSON file
  (paths starting with `~` are expanded). A relative path resolves against the
  TOML file's own directory, which is what lets the deployed config on the Pi
  refer to its credentials as bare filenames like `"reddit.json"`.
- `excluded_url_host_keywords` are case-insensitive substring matches against
  the URL hostname only. `"insider"` blocks `www.businessinsider.com`;
  `"businessinsider.com"` blocks that domain and its subdomains.
- `excluded_url_or_description_keywords` are case-insensitive: URL matches are
  substrings, description matches are whole-word. `"politics"` blocks URLs
  containing `/politics/` and descriptions containing the word `politics`.

### RSS feeds

`catalog.rss_feeds` is the source of truth for which feeds get polled. Each row
tracks the URL, an `enabled` flag, and a `deleted_at` tombstone. Per-feed health
(etag / last-modified cache headers, consecutive failure count, last error, last
status) lives in `content.rss_feed_health`, keyed by `normalized_url`, so the
admin-owned catalog and the publisher-owned health data stay independently
writable. Feeds are **not** auto-disabled on failure: the publisher keeps
counting `consecutive_failures` + `last_error`, and the web admin surfaces
persistently failing feeds for manual removal.

First-time seeding is a publisher command:

```bash
cd ingest
export FETCHLINKS_DATABASE_URL='postgresql://...'
python3 publish_tool.py bootstrap-catalog
```

To grow an already-live catalog, `rss_feed_import.py` validates candidates over
the network first:

```bash
# Validate candidate URLs over the network, then insert the survivors:
python3 rss_feed_import.py --input /tmp/rss-list.txt

# Same but dry-run first; produces /tmp/rss-list.txt.pruned for review.
python3 rss_feed_import.py --input /tmp/rss-list.txt --dry-run
python3 rss_feed_import.py --pruned /tmp/rss-list.txt.pruned
```

Neither command revives a feed the admin has tombstoned; restoring one is an
explicit action in the web admin.

Use `--abandoned-days N` to change the cutoff for rejecting feeds with no
recent posts.

A snapshot of the catalog is written by `export_rss_feeds.py` to
`[sources.rss].export_path` (three sections: active feeds, commented disabled
feeds with their failure reason, commented tombstoned feeds). In dev that path
is `data/config/rss_feeds.txt`, so the seed file stays aligned with catalog
changes and can be committed after review.

## 5) Prepare the database and catalog (first run only)

`fetch_links.py` does **not** read `rss_feeds.txt` directly. It reads
`runtime/catalog/catalog.v1.json`, which the publisher exports from the
database. `[sources.rss].seed_file` is consumed only by `bootstrap-catalog`.

```bash
cd ingest
export FETCHLINKS_DATABASE_URL='postgresql://...'   # owner role
python3 publish_tool.py migrate
python3 publish_tool.py bootstrap-catalog
python3 publish_tool.py sync-catalog
```

`bootstrap-catalog` only ever inserts, so it is safe to re-run; it will not
re-enable or resurrect anything. See [../db/README.md](../db/README.md) for the
schema and the role credentials.

To exercise collection on a machine that has never talked to a database, build
a catalog straight from the seed files instead:

```bash
python3 catalog_tool.py build-from-seeds
```

## 6) Run the collector

```bash
cd ingest
python3 fetch_links.py
```

The default config file is `data/config/fetchlinks.toml`. To use a different
file, pass `--config /path/to/fetchlinks.toml`.

The collector opens no database. It writes a batch to
`runtime/outbox/ready/<batch-id>/` and stops there; publishing is a separate
step:

```bash
python3 publish_tool.py publish
```

## 7) Validate output

- `python3 publish_tool.py status` reports queue depth and database totals.
- `python3 spool_tool.py list ready` shows what is waiting to be published.
- Logs are written to `[paths].log_file` at `[paths].log_level`.
