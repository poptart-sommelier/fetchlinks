# Working in this repo

Read this before changing anything. It records decisions that are invisible in
the code and expensive to rediscover — several were found by breaking them.

For what the system *is*, read [OVERVIEW.md](OVERVIEW.md) (architecture, roles,
cost) and [README.md](README.md) (layout, commands). This file is about how to
work here, and what not to undo.

## This is a hobby project

One person runs this for their own use. It is not a product, has no users
besides its owner, no SLA, no on-call, and no availability target.

That is not an excuse for sloppy work — the correctness bar is high, because
bugs here cost the owner's evenings. But it does mean **the failure modes that
justify most engineering effort do not apply**:

- **Downtime is fine.** If the site is down for a day, nobody is paged and
  nothing is lost. Do not add redundancy, failover, health checks, or
  self-healing.
- **Losing all collected posts is an accepted outcome**, stated explicitly by
  the owner. Posts are a one-month rolling window that deletes itself and
  refills within the hour. **There are no backups, by decision.** Do not propose
  them, do not add them, do not treat their absence as a gap.
- **There is no alerting, by decision.** No new articles on the site is the
  visible symptom of every failure that matters. A monitoring stack would only
  restate what the front page already shows. Do not add one.
- **Cost matters more than robustness.** Everything runs inside free tiers, and
  staying there is a real design constraint (see Cost in OVERVIEW.md). A change
  that improves resilience but wakes the database more often is a bad trade.

Before proposing work, ask whether it protects against something the owner has
already said they don't care about. If so, don't.

Prefer the boring, small solution. Complexity that would pay for itself across a
team of ten is pure cost here.

## plan.md is the source of truth

`plan.md` in the repo root holds the numbered architecture decisions and the
outstanding work. It outranks your own reasoning: where it contradicts what
seems sensible now, the decision wins until it is explicitly revisited.

It is **gitignored**, so a fresh clone will not have it. If it is missing, say
so rather than guessing at the decisions it holds.

Write back to it when work settles a question. A decision discovered and not
recorded is one that gets re-litigated in a month.

**Keep `plan.md` to outstanding work only.** When something is finished, move
its section out to `plan_done.md` rather than marking it DONE in place. A plan
that lists mostly finished work stops being readable as a plan, and the answer
to "what needs doing?" should be visible without scrolling past a year of
history.

Move the *reasoning*, not a one-line summary. `plan_done.md` exists because the
why behind a change is not recoverable from the code, and several entries there
were learned by breaking something first. Consult it before changing anything
that looks arbitrary — the odds are good it is load-bearing and the note says
why. Both files are gitignored.

## Invariants that must not be quietly undone

Each of these cost real debugging. Changing one is allowed; changing one by
accident is the thing to avoid.

**The collector never touches a database.** `ingest/fetch_links.py` and
everything it calls write batches to disk and hold no connection string. The
boundary is enforced by `fetchlinks-collect.service` having **no
`EnvironmentFile`**. That absence is the whole mechanism — do not add one "for
consistency". It is why collection survives a database outage, and why the Pi
holding a residential IP does not also hold write access to production.

**The publisher uses the DIRECT Neon endpoint, never the pooled one.** psycopg3
promotes repeated statements to server-side prepared statements, which do not
survive PgBouncer in transaction mode. Pooling exists for the web app's many
short-lived connections. Getting this wrong fails intermittently under load,
which is the worst way for it to fail.

**`psycopg[binary]` cannot be used on the Pi.** Raspberry Pi OS runs a 64-bit
kernel with a 32-bit userland: `uname -m` reports `aarch64` but
`dpkg --print-architecture` reports `armhf`, and pip resolves `armv8l` wheels,
for which `psycopg-binary` publishes none. This **cannot** be expressed as a PEP
508 marker, because `platform_machine` reports the kernel and would select the
missing wheel. `deploy/bootstrap.sh` branches on `dpkg --print-architecture` and
installs `libpq5` instead.

**Republishing a batch must never duplicate content.** The spool retries on any
interruption, so every apply path is idempotent by design. If you touch
`ingest/publisher/`, prove replay safety with a test rather than reasoning about
it — a bug here is silent and cumulative.

**Runtime state lives in one gitignored directory.** Everything mutable on the
Pi is under `~/fetchlinks/runtime/`. There is no `/opt`, `/var/lib` or `/etc`
fragment. `bootstrap.sh` never overwrites anything there, which is what makes it
safe to re-run — and why it reports when a template gains a setting the
deployment lacks.

**Migrations are the only thing that changes schema.** Runtime roles cannot
perform DDL, and each role is deliberately blind to the other's tables: the
publisher writes content and cannot touch the catalog; the web app writes
catalog identity and cannot touch content. See [db/README.md](db/README.md).

## Nothing deploys itself, except Vercel

- **Vercel**: pushing to `master` triggers a production build automatically.
  Any other branch gets a preview on the development database. Merging is
  therefore deploying — be sure before you merge.
- **The Pi**: nothing updates itself. Code reaches it only by
  `cd ~/fetchlinks && git pull && ./deploy/bootstrap.sh`. This is deliberate:
  unattended pulls on the one host holding a production credential would run
  anything merged to `master` within the hour.
- **Catalog data** does sync automatically, hourly, from Neon to the Pi. Feeds
  added in the web admin need no deployment.

## Validation

Run what already exists; do not add new tooling to run it.

```bash
cd ingest && python -m unittest discover tests   # ~590 tests, seconds
cd web && npm run validate                       # lint, typecheck, vitest, build
```

Tests needing a real database skip unless `FETCHLINKS_TEST_DATABASE_URL` is set,
so a checkout with no database still validates cleanly. Skips are expected; a
run reporting only passes and skips is a good run.

Dependency changes require the full `npm run validate`, not just `npm audit`.

## Conventions

Comments explain **why**, never what. If a line needs a comment to say what it
does, rename something instead. Load-bearing oddities — a `-` prefix on a
systemd `ExecStart`, an ordering dependency, a workaround for someone else's bug
— must say why they are there, because they look like mistakes otherwise.

Commit messages are prose, not bullet lists of changed files. Say what changed,
and why it needed to change. If it fixes something subtle, explain the failure
it prevents so the next reader does not reintroduce it.

Do not add dependencies, linters, formatters, or CI without being asked. Do not
create markdown files for planning or notes.

Fix bugs you cause or that are tightly coupled to your change. Leave unrelated
ones alone, but mention them.
