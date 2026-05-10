# Next.js Repository Layout Decision

This document records the Step 3 layout decision for the Next.js migration. It does not move files or scaffold the app.

## Decision

Build the new Next.js application under a new `web/` directory at the repository root.

Update: the legacy Flask app was removed after its behavior was captured in `flask_baseline.md`. The active app now lives in `web/`.

Current shape:

```text
fetchlinks_webapp/
  flask_baseline.md            # current Flask baseline notes
  nextjs_layout.md             # this decision record
  README.md
  web/                         # active Next.js app
    package.json
    src/
    ...
```

## Why `web/`

- Kept the Flask app runnable during the early migration.
- Avoids mixing Python package files and Node/Next.js project files at the root during early migration.
- Made side-by-side validation straightforward while Flask still existed.
- Let us remove Flask later without moving the Next.js project.
- Keeps rollback simple because Step 4 and later changes are isolated under `web/` unless a root-level deployment or documentation file is intentionally changed.

## Alternatives Considered

### Scaffold Next.js At The Repository Root

Pros:

- Standard shape for a pure Next.js repository.
- Fewer nested commands after Flask is removed.

Cons:

- Would have immediately mixed Next.js files with Flask files.
- Makes the early migration harder to review.
- Would have raised the chance of disrupting the working Flask app before the replacement was validated.

### Move Flask To `legacy-flask/` Before Scaffolding

Pros:

- Gives the repository root to the new app.
- Makes the desired final state visible early.

Cons:

- Creates a large file-move diff before the new app exists.
- Adds risk to an otherwise documentation/scaffold phase.
- Makes side-by-side validation slightly more awkward.

## Flask Cleanup

The Flask files were removed once the project no longer needed side-by-side execution. The baseline document remains because it records the old routes and user-facing behavior needed for the rewrite.

## Testing Impact

No automated tests are expected for this step because it only records a layout decision. Tests begin when implementation behavior is introduced under `web/`.

## Step 3 Validation

This step is complete when:

- The layout decision is documented.
- No application files have been moved.
- No Next.js scaffold has been created yet.
- The branch is committed, pushed, merged, cleaned up, and `master` is clean.
