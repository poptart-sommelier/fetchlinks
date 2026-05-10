# Flask Baseline

This document captures the current `fetchlinks_webapp` Flask implementation before the Next.js migration. It is intentionally descriptive: no behavior is changed in this step.

Status: the Flask implementation has since been removed. This document remains as the reference for old routes, templates, model assumptions, and behavior to preserve in the Next.js rewrite.

## Current Entry Points

- `fetchlinks_webapp.py` imports the Flask app, SQLAlchemy handle, and shell context models.
- `.flaskenv` sets `FLASK_APP=fetchlinks_webapp.py`.
- `twitter_web_app.py` appears to be an older alternate entry point and imports a `Urls` model that is not present in `app/models.py`.

## Current Runtime And Dependencies

- Framework: Flask.
- Database layer: Flask-SQLAlchemy.
- Template dependency: Flask-Bootstrap.
- Local development command from `README.md`:
  - `python -m flask run --host=0.0.0.0 --port=8080`
- Old VM deployment example: `fetchlinks_webapp.service.bak` runs Flask directly under systemd.

## Current Configuration

- `config.py` hardcodes `database = '/your/path/here/fetchlinks.db'`.
- `SQLALCHEMY_DATABASE_URI` is built from that hardcoded SQLite path.
- `POSTS_PER_PAGE = 50`.
- There is no environment-based DB path configuration today.

## Current Flask App Structure

- `app/__init__.py` creates a global Flask app object.
- `app/__init__.py` creates a global SQLAlchemy object.
- `app/__init__.py` initializes Flask-Bootstrap.
- `app/routes.py` registers routes directly on the global app object.
- There is no app factory pattern.
- There are no tests in the current webapp repository.

## Current Routes

### `/` and `/index`

Implemented in `app/routes.py`.

Current behavior:

- Reads `page` from the query string, defaulting to page 1.
- Queries `Posts` ordered by `date_created` descending.
- Paginates using `POSTS_PER_PAGE`.
- Queries the latest `Twitter.time_created` as `last_update`.
- Renders `app/templates/index.html`.
- Returns a `Cache-Control: max-age=900` header.

Risks:

- If the `Twitter` table is empty, `last_update[0]` will raise.
- The current ingestion app no longer uses Twitter state as the main freshness marker.
- The route does not match the current normalized URL schema.

### Commented user route

`app/routes.py` contains a commented `/user/<screen_name>` route from an older Twitter-oriented design. It is not active behavior to preserve.

## Current Templates

- `app/templates/base.html`
  - Extends `bootstrap/base.html`.
  - Displays a Home link.
  - Displays `Last Update` on the right.
  - Defines the main `{% block content %}`.
- `app/templates/index.html`
  - Loops through posts.
  - Includes `_post.html` for each post.
  - Shows Newer/Older pagination links.
- `app/templates/_post.html`
  - Displays description, author, source, direct link, date created, and links.
  - Expects denormalized fields `url_1` through `url_6`.
- `app/templates/user.html`
  - References the inactive user route pattern.

## Current Models

Defined in `app/models.py`.

### `Posts`

Expected fields:

- `idx`
- `source`
- `author`
- `description`
- `direct_link`
- `date_created`
- `unique_id_string`
- `url_1` through `url_6`
- `urls_missing`

Mismatch with current ingestion schema:

- Current ingestion stores post metadata in `posts`.
- Current ingestion stores URLs in `post_urls`.
- Current ingestion no longer stores `url_1` through `url_6` columns.

### `Twitter`

Expected fields:

- `idx`
- `last_accessed_id`
- `time_created`

Mismatch with current ingestion schema:

- Current ingestion has source-specific state tables such as `rss_feed_state`, `reddit_state`, `bluesky_state`, and `mastodon_state`.
- The webapp should not depend on Twitter state for last update.

## Unused Or Broken Leftovers

- `app/forms.py` imports `Users`, but no `Users` model exists.
- `app/forms.py` appears to be unused auth/profile tutorial code.
- `twitter_web_app.py` imports `Urls`, but no `Urls` model exists.
- `fetchlinks_webapp.service.bak` points to old local paths and runs Flask directly with `flask run`.

## Behavior To Preserve In The Next.js App

The replacement should preserve these user-facing basics:

- Latest posts first.
- Pagination.
- Post description.
- Source link.
- Author.
- Direct link to the source post.
- Date created.
- Extracted URLs for each post.
- A notion of recent update or database freshness, redesigned around the current ingestion schema.

## Behavior Not Required To Preserve

- Twitter-specific user route.
- `Twitter` model dependency.
- `url_1` through `url_6` model fields.
- Flask-Bootstrap templates.
- Unused auth/profile forms.
- Hardcoded SQLite path in `config.py`.
- Running Flask directly through `flask run` in production.

## Next.js Migration Implications

The new app should:

- Read SQLite in read-only mode.
- Query the normalized `posts` and `post_urls` tables.
- Group URLs per post in the web read model.
- Avoid mutating ingestion tables or source state.
- Use environment-based DB configuration.
- Build tests as each query and route is introduced.

## Step 2 Validation

This step only documents the current baseline. There are no code or runtime behavior changes, so no automated tests are expected for this step.
