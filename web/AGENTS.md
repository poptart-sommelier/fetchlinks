# Working in `web/`

The project's own guidance is in [`../AGENTS.md`](../AGENTS.md) — read that
first. This file exists because `next dev` writes the block below on every run
if it is missing, so it is committed rather than left to reappear as noise in
every diff. There is no configuration option to turn it off.

Anything outside the markers is preserved when Next rewrites the block.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
