# Database

The PostgreSQL schema for Fetchlinks. Shared by both components, owned by
neither: the Publisher writes `content`, the web admin writes `catalog`.

## Layout

| Schema    | Owner            | Contents                                          |
| --------- | ---------------- | ------------------------------------------------- |
| `catalog` | web admin        | RSS feed and subreddit identity plus on/off state  |
| `content` | Publisher        | posts, URLs, feed health, checkpoints, follows     |
| `public`  | migrations       | `schema_migrations` only                          |

## Migrations

Plain SQL, applied in filename order, recorded with a checksum:

```
db/migrations/0001_schemas_and_catalog.sql
db/migrations/0002_content.sql
db/migrations/0003_roles_and_grants.sql
```

Rules:

- **Never edit an applied migration.** The runner stores a SHA-256 of each file
  and refuses to continue when one changes, because an edited migration is the
  quiet way environments drift apart. Add a new one instead.
- **Runtime applications never issue DDL.** Neither the Publisher nor the web
  app creates or alters tables; the schema in this directory is the schema in
  the database.
- File names must match `NNNN_lower_snake_case.sql`.

Apply them with the owner credentials:

```powershell
$env:FETCHLINKS_DATABASE_URL = 'postgresql://owner:...@host/fetchlinks'
python ingest/publish_tool.py migrate --dry-run   # what would run
python ingest/publish_tool.py migrate
```

## Roles

Migration `0003` creates three roles **without passwords**. Setting them is a
deliberate operator step so that no credential can arrive through this
repository:

```sql
ALTER ROLE fetchlinks_web       WITH PASSWORD '...';
ALTER ROLE fetchlinks_publisher WITH PASSWORD '...';
```

| Role                   | Used by    | Can do                                              |
| ---------------------- | ---------- | --------------------------------------------------- |
| `fetchlinks_owner`     | migrations | everything; never used at runtime                   |
| `fetchlinks_web`       | Vercel     | read everything; insert/update `catalog` only        |
| `fetchlinks_publisher` | the Pi     | read `catalog`; insert/update/delete `content`       |

Two properties are enforced by grants rather than by code, because that is the
only place they hold under a bug or a compromise:

- The web role has **no DELETE on the catalog**, so removing a feed is
  necessarily a soft delete.
- The publisher role has **no write access to the catalog**, so the Pi can
  never edit the list the admin curates.

Vercel gets a pooled `fetchlinks_web` URL; the Pi gets a direct TLS
`fetchlinks_publisher` URL. Owner credentials stay with the operator.

## Running the schema locally

The integration tests need a real server. Anything disposable will do:

```powershell
docker run -d --name fetchlinks-pg `
    -e POSTGRES_PASSWORD=fetchlinks -e POSTGRES_DB=fetchlinks `
    -p 55432:5432 postgres:17

$env:FETCHLINKS_TEST_DATABASE_URL = 'postgresql://postgres:fetchlinks@localhost:55432/postgres'
cd ingest; python -m unittest discover -s tests -t .
```

The harness creates and migrates its own `fetchlinks_test` database, so the URL
above can point anywhere. Without the variable the PostgreSQL tests skip rather
than fail.

## Notes on the design

- **`timestamptz` everywhere.** The batch contract carries RFC 3339 UTC; a
  naive column would silently reinterpret it as local time.
- **Natural keys join across ownership boundaries.** `content.rss_feed_health`
  is keyed by `normalized_url`, not by `catalog.rss_feeds.feed_id`, so health
  survives a feed being removed and re-added and the two schemas stay
  independently writable.
- **`content` column names follow the contract** (`unique_id`, `posted_at`,
  `observed_at`) rather than the old SQLite names (`unique_id_string`,
  `date_created`, `time_created`), so the Publisher can be checked against
  `ingest/pipeline/contract.py` by eye.
- **`source_type` is constrained by shape, not by a value list.** A CHECK was
  chosen over an enum precisely so that adding a source needs no migration; a
  whitelist would have thrown that away.
- **`content.published_batches` is the replay guard.** The Publisher claims a
  batch id there in the same transaction as the batch's content, so a run that
  commits and then fails to archive the batch costs nothing on retry.
- **`content.follows_snapshots` is the staleness guard.** Follows arrive as
  complete snapshots, so applying one is a replacement; recording when each
  scope was last observed stops a delayed batch reinstating a superseded list.
