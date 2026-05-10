# Fetchlinks

Fetchlinks collects posts with external links and presents them in a web UI.

The project is organized as a small monorepo with two runtime apps:

- `ingest/` - Python app that gathers links and writes the SQLite database.
- `web/` - Next.js app that reads the SQLite database and renders the UI.

The shared boundary between the apps is the SQLite database. The ingest app owns
creating and updating the database; the web app opens it read-only via the
`FETCHLINKS_DB` environment variable.

## Quick Start

Install and run the ingest app:

```bash
python3 -m venv ../venv
source ../venv/bin/activate
cd ingest
pip install -r requirements.txt
cd fetchlinks
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
cd ingest/fetchlinks
python -m unittest discover tests
```

Run web validation:

```bash
cd web
npm run validate
```

For detailed setup notes, see `ingest/SETUP.md` and `web/README.md`.