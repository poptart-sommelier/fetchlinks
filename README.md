# Fetchlinks

Fetchlinks collects posts with external links and presents them in a web UI.

The project is organized as a small monorepo with two runtime apps:

- `ingest/` - Python app that gathers links and writes the SQLite database.
- `web/` - Next.js app that reads the SQLite database and renders the UI.

The shared boundary between the apps is the SQLite database. The ingest app owns
creating and updating the database; the web app opens it read-only via the
`FETCHLINKS_DB` environment variable.

For project orientation (goal, architecture, cost, layout, security) see
[OVERVIEW.md](OVERVIEW.md). For deploy and operations see
[deploy/README.md](deploy/README.md).

## Quick Start

Install and run the ingest app. Create the virtualenv at `.venv` in the
repo root — that's the path the VS Code workspace (`.vscode/settings.json`,
`.vscode/tasks.json`) and the production layout (`/opt/fetchlinks/.venv`)
both expect:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r ingest/requirements.txt
cd ingest
python3 fetch_links.py
```

Run the web app against an existing database:

```bash
cd web
npm install
FETCHLINKS_DB=/absolute/path/to/fetchlinks.db npm run dev
```

## Validation

Run Python tests:

```bash
cd ingest
python -m unittest discover tests
```

Run web validation:

```bash
cd web
npm run validate
```

## Deployment Examples

Example systemd and nginx files for running the web app on a VM live in
`deploy/`. They are adapted for a single checkout at `/opt/fetchlinks` with the
web app at `/opt/fetchlinks/web`.

For detailed setup notes, see `ingest/SETUP.md` and `web/README.md`.