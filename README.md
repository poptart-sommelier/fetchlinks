# Fetchlinks

Fetchlinks collects posts with external links and presents them in a web UI.

The project is organized as a small monorepo with two runtime apps:

- `ingest/` - Python collector and publisher. The **collector** fetches from
  every source and writes versioned batches to disk; the **publisher** reads
  those batches and applies them to PostgreSQL. The collector never opens a
  database, so it runs anywhere and keeps working when the database is down.
- `db/` - SQL migrations, roles, and grants. Runtime apps never alter schema.
- `web/` - Next.js app that reads PostgreSQL and renders the UI.

The shared boundary between the two ingest halves is the on-disk batch contract
in `ingest/pipeline/schemas/`; the boundary between ingest and web is
PostgreSQL. Both apps take a `DATABASE_URL`, but different ones: the publisher
connects as `fetchlinks_publisher` (writes content, cannot touch the catalog)
and the web app as `fetchlinks_web` (writes feed and subreddit identity, cannot
touch content). See [db/README.md](db/README.md).

The production target is Neon PostgreSQL with the web app on Vercel and the
collector on a home Raspberry Pi, so requests to sources originate from a
residential connection.

For project orientation (goal, architecture, cost, layout, security) see
[OVERVIEW.md](OVERVIEW.md). For deploying and operating the Pi, see
[deploy/README.md](deploy/README.md).

## Quick Start

Install the ingest app. Create the virtualenv at `.venv` in the repo root —
that's the path the VS Code workspace (`.vscode/settings.json`,
`.vscode/tasks.json`) expects:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r ingest/requirements.txt
```

Prepare the database, then collect and publish. Collection and publication are
separate commands on purpose: the first needs source credentials and no
database, the second needs a database and no credentials.

```bash
cd ingest
python3 publish_tool.py migrate            # apply db/migrations
python3 publish_tool.py bootstrap-catalog  # seed feeds and subreddits
python3 publish_tool.py sync-catalog       # export the catalog snapshot
python3 fetch_links.py                     # collect -> runtime/outbox/ready
python3 publish_tool.py publish            # drain ready batches into PostgreSQL
```

`spool_tool.py list ready` and `publish_tool.py status` show what is queued and
what has landed. See `ingest/SETUP.md`.

Run the web app:

```bash
cd web
npm install
DATABASE_URL='postgresql://...' npm run dev
```

## Validation

Run Python tests:

```bash
cd ingest
python -m unittest discover tests
```

The publisher's integration tests run against a real disposable PostgreSQL and
skip unless `FETCHLINKS_TEST_DATABASE_URL` is set.

Run web validation:

```bash
cd web
npm run validate
```

The web query tests use the same variable and skip the same way, so a checkout
with no database still validates.

## Deployment Examples

`deploy/` still contains the systemd and nginx examples for the previous
single-host VM deployment. They are superseded by the Neon/Vercel/Pi deployment
and will be replaced once it has been demonstrated end to end.

For detailed setup notes, see `ingest/SETUP.md` and `web/README.md`.