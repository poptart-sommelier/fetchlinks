# Fetchlinks Web

This is the Next.js TypeScript application for the Fetchlinks web UI. It reads the SQLite database written by the ingest app.

## Runtime

The scaffold targets Node 24.15 or newer with npm 11.12 or newer.

## Environment

Copy `.env.example` to `.env.local` for local development and set `FETCHLINKS_DB` to the absolute path of the SQLite database written by the fetchlinks ingestion app. The web app treats this database as read-only.

SQLite access uses Node's built-in `node:sqlite` module, so no external SQLite npm package or native build step is required.

## Commands

```bash
npm install
npm run dev
npm run lint
npm run typecheck
npm run test
npm run build
npm run validate
npm run validate:production
```

The development server listens on http://localhost:3000 by default.

`npm run validate` runs lint, typecheck, tests, and the production build in order.

`npm run validate:production` runs the same validation sequence, then starts `npm run start` on a local ephemeral port with a temporary SQLite fixture database and fetches the rendered home page.

To check production mode manually against a real database:

```bash
FETCHLINKS_DB=/absolute/path/to/fetchlinks.db npm run build
FETCHLINKS_DB=/absolute/path/to/fetchlinks.db npm run start -- --hostname 127.0.0.1 --port 3000
```

Historical notes from the Flask-to-Next.js migration live in `docs/`.
