# Fetchlinks Web

The Next.js TypeScript application for the Fetchlinks web UI. It reads the
PostgreSQL database that the publisher writes.

## Runtime

Node 24.15 or newer with npm 11.12 or newer.

## Environment

Copy `.env.example` to `.env.local` and set `DATABASE_URL` to the PostgreSQL
connection string for the `fetchlinks_web` role. That is the only database
setting: there is no file path, and the collector's source credentials never
reach this application.

The application connects with `@neondatabase/serverless`, which issues each
query as an HTTP request rather than holding a connection. That suits Vercel
functions, where a pooled TCP connection would be established and discarded per
invocation, but it also means the driver only speaks to a Neon endpoint. Point
`DATABASE_URL` at your Neon **development** branch for local work; a plain local
PostgreSQL will not answer it.

The schema lives in [`../db/migrations`](../db/migrations) and is applied by the
publisher, not by this application. See [`../db/README.md`](../db/README.md) for
the table layout and for which role each connection string should use.

Privileges do the enforcing here, not convention. `fetchlinks_web` may read
everything and write feed and subreddit identity, but it holds no DELETE on the
catalog, so the soft delete the admin UI performs is the only delete available
to it.

To enable the admin UI, also set `FETCHLINKS_ADMIN_USER` and
`FETCHLINKS_ADMIN_PASS`. Requests to `/admin/*` are gated by HTTP Basic auth
against those values. If either is unset, the admin routes return HTTP 503.

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

`npm run validate` runs lint, typecheck, tests, and the production build.

### Tests

The query modules are tested against a real, disposable PostgreSQL rather than a
mock, because what they rely on — `ON CONFLICT`, a cross-schema `LEFT JOIN`,
boolean and `timestamptz` coercion, `to_char` formatting — is behaviour a fake
would only pretend to have. Set `FETCHLINKS_TEST_DATABASE_URL` and they run;
leave it unset and they skip, so a checkout with no database still runs the pure
suites.

These tests connect over TCP with `pg`, so any local PostgreSQL works:

```bash
docker run -d --name fetchlinks-pg -e POSTGRES_PASSWORD=fetchlinks -p 5432:5432 postgres:17
docker exec fetchlinks-pg createdb -U postgres fetchlinks_web_test
FETCHLINKS_TEST_DATABASE_URL=postgresql://postgres:fetchlinks@localhost:5432/fetchlinks_web_test npm run test
```

Only the transport differs from production; the SQL under test is identical.
The Neon driver itself is exercised by the production smoke test.

### Production smoke test

`npm run validate:production` runs the full validation sequence, then serves the
production build against a real Neon branch and fetches the rendered home page.
It is the only check that exercises the real driver, the real connection string
and the real build together.

It needs two connection strings, both pointing at the **development** branch:

- `FETCHLINKS_SMOKE_DATABASE_URL` — owner or publisher role. Inserts a uniquely
  named post and deletes it again.
- `FETCHLINKS_SMOKE_WEB_DATABASE_URL` — the `fetchlinks_web` role. What the
  application itself runs as.

They are deliberately separate. Serving the app as the owner would pass while
proving nothing, because a missing grant on the web role — the failure this is
meant to catch — is invisible to a role that can already read everything. The
schema must already exist; migrations are applied by `publish_tool.py migrate`
as the owner, not by this test.

To run production mode manually:

```bash
DATABASE_URL='postgresql://...' npm run build
DATABASE_URL='postgresql://...' npm run start -- --hostname 127.0.0.1 --port 3000
```

Historical notes from the Flask-to-Next.js migration live in `docs/`.
