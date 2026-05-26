import type { ReactNode } from "react";

import Link from "next/link";

import type { RssFeed, RssFeedStatus } from "../../../models/rss-feeds";
import {
  countRssFeedsByStatus,
  listRssFeeds,
  withWritableFetchlinksDatabase,
  type RssFeedCounts,
} from "../../../server/feeds";
import { loadAppConfig } from "../../../server/config";
import { formatRelative } from "../../../lib/format-relative";
import {
  addFeedAction,
  deleteFeedAction,
  restoreFeedAction,
} from "./actions";

type PageSearchParams = Record<string, string | string[] | undefined>;

type AdminFeedsPageProps = {
  searchParams?: Promise<PageSearchParams>;
};

type LoadResult =
  | { status: "ready"; feeds: RssFeed[]; counts: RssFeedCounts; filters: ActiveFilters }
  | { status: "error" };

type ActiveFilters = {
  status: RssFeedStatus | "all";
  q?: string;
  errors: boolean;
};

export const dynamic = "force-dynamic";

export default async function AdminFeedsPage({
  searchParams,
}: AdminFeedsPageProps = {}) {
  const resolved = await searchParams;
  const filters = parseFilters(resolved);
  const result = loadFeeds(filters);
  return <AdminFeedsView result={result} />;
}

function loadFeeds(filters: ActiveFilters): LoadResult {
  try {
    const config = loadAppConfig(process.env);
    return withWritableFetchlinksDatabase(config, (db) => ({
      status: "ready" as const,
      feeds: listRssFeeds(db, filters),
      counts: countRssFeedsByStatus(db),
      filters,
    }));
  } catch {
    return { status: "error" };
  }
}

export function AdminFeedsView({ result }: { result: LoadResult }) {
  if (result.status === "error") {
    return (
      <main className="shell">
        <header className="page-header">
          <div className="page-title">
            <p className="eyebrow">Fetchlinks admin</p>
            <h1>RSS feeds</h1>
          </div>
        </header>
        <section className="state state-error" role="alert">
          <h2>Feeds are unavailable</h2>
          <p>The database could not be opened. Check the server configuration.</p>
        </section>
      </main>
    );
  }

  const { feeds, counts, filters } = result;

  return (
    <main className="shell">
      <header className="page-header">
        <div className="page-title">
          <p className="eyebrow">
            <Link href="/admin">&larr; Admin</Link>
          </p>
          <h1>RSS feeds</h1>
        </div>
        <div className="feed-counts-tile" aria-label="Feed totals">
          <CountTile
            count={counts.active}
            label={counts.active === 1 ? "active" : "active"}
            href="/admin/feeds?status=active"
            tone="ok"
          />
          <CountTile
            count={counts.errors}
            label={counts.errors === 1 ? "error" : "errors"}
            href="/admin/feeds?errors=1"
            tone={counts.errors > 0 ? "error" : "muted"}
          />
        </div>
      </header>

      <form action={addFeedAction} aria-label="Add feed" className="add-form">
        <label className="add-form-field">
          <span>Add feed</span>
          <input
            name="feed_url"
            placeholder="https://example.com/feed.xml"
            required
            type="url"
          />
        </label>
        <button className="add-form-btn" type="submit">
          Add feed
        </button>
      </form>

      <form action="/admin/feeds" aria-label="Search feeds" className="search-form" method="get">
        <label className="search-form-field">
          <span>Search</span>
          <input
            defaultValue={filters.q ?? ""}
            name="q"
            placeholder="Feed URL substring"
            type="search"
          />
        </label>
        {filters.status !== "all" ? (
          <input name="status" type="hidden" value={filters.status} />
        ) : null}
        {filters.errors ? <input name="errors" type="hidden" value="1" /> : null}
        <button className="search-form-btn" type="submit">
          Search
        </button>
        {filters.status !== "all" || filters.q || filters.errors ? (
          <Link className="clear-filters" href="/admin/feeds">
            Clear
          </Link>
        ) : null}
      </form>

      {feeds.length === 0 ? (
        <section className="state">
          <h2>No feeds</h2>
          <p>No rows match the current filters.</p>
        </section>
      ) : (
        <section className="post-list" aria-label="RSS feeds">
          {feeds.map((feed) => (
            <FeedRow key={feed.id} feed={feed} />
          ))}
        </section>
      )}
    </main>
  );
}

function FeedRow({ feed }: { feed: RssFeed }) {
  return (
    <article className="post-item feed-row">
      <header className="feed-row-header">
        <a
          className="post-source feed-row-url"
          href={feed.feedUrl}
          rel="noreferrer"
          target="_blank"
          title={feed.feedUrl}
        >
          <FeedUrlLabel url={feed.feedUrl} />
        </a>
        <FeedStatusPill feed={feed} />
      </header>
      <div className="feed-row-footer">
        <FeedStats feed={feed} />
        <nav aria-label="Feed actions" className="post-links feed-row-actions">
          {feed.status !== "removed" ? (
            <FeedAction
              action={deleteFeedAction}
              feedId={feed.id}
              label="Remove feed"
            />
          ) : null}
          {feed.status === "removed" ? (
            <FeedAction action={restoreFeedAction} feedId={feed.id} label="Restore" />
          ) : null}
        </nav>
      </div>
    </article>
  );
}

function FeedStats({ feed }: { feed: RssFeed }) {
  const items: ReactNode[] = [];

  const fetchedLabel = formatRelative(feed.lastFetchedAt);
  if (fetchedLabel) {
    items.push(
      <span
        key="fetched"
        className="feed-stat"
        title={feed.lastFetchedAt ?? undefined}
      >
        Fetched <strong>{fetchedLabel}</strong>
      </span>,
    );
  }

  // Only surface last-success when it differs from last-fetched
  // (i.e., the most recent attempt failed).
  const fetchedAndSuccessDiffer =
    feed.lastSuccessAt != null &&
    feed.lastFetchedAt != null &&
    feed.lastSuccessAt !== feed.lastFetchedAt;
  const successLabel = fetchedAndSuccessDiffer
    ? formatRelative(feed.lastSuccessAt)
    : null;
  if (successLabel) {
    items.push(
      <span
        key="success"
        className="feed-stat"
        title={feed.lastSuccessAt ?? undefined}
      >
        Last ok <strong>{successLabel}</strong>
      </span>,
    );
  }

  const entryLabel = formatRelative(feed.latestEntryAt);
  if (entryLabel) {
    items.push(
      <span
        key="entry"
        className="feed-stat"
        title={feed.latestEntryAt ?? undefined}
      >
        Newest entry <strong>{entryLabel}</strong>
      </span>,
    );
  }

  if (feed.consecutiveFailures > 0) {
    items.push(
      <span key="fail" className="feed-stat feed-stat-danger">
        <strong>{feed.consecutiveFailures}</strong>{" "}
        consecutive {feed.consecutiveFailures === 1 ? "failure" : "failures"}
      </span>,
    );
  }

  if (items.length === 0) return null;
  return <div className="feed-stats">{items}</div>;
}

type FeedHealth = "healthy" | "unhealthy" | "removed";

const HEALTH_PILL_LABEL: Record<FeedHealth, string | null> = {
  healthy: null,
  unhealthy: "Unhealthy",
  removed: "Removed",
};

function getFeedHealth(feed: RssFeed): FeedHealth {
  if (feed.status === "removed") return "removed";
  if (feed.consecutiveFailures > 0) return "unhealthy";
  if (feed.lastStatus !== null && feed.lastStatus >= 400) return "unhealthy";
  return "healthy";
}

function buildUnhealthyTitle(feed: RssFeed): string | undefined {
  const parts: string[] = [];
  if (feed.lastStatus !== null) parts.push(`HTTP ${feed.lastStatus}`);
  if (feed.consecutiveFailures > 0) {
    parts.push(
      `${feed.consecutiveFailures} consecutive ${
        feed.consecutiveFailures === 1 ? "failure" : "failures"
      }`,
    );
  }
  if (feed.lastError) parts.push(feed.lastError);
  return parts.length ? parts.join(" \u2022 ") : undefined;
}

export function FeedStatusPill({ feed }: { feed: RssFeed }) {
  const health = getFeedHealth(feed);
  const label = HEALTH_PILL_LABEL[health];
  if (!label) return null;
  const title = health === "unhealthy" ? buildUnhealthyTitle(feed) : undefined;
  return (
    <span className={`status-pill status-pill-${health}`} title={title}>
      {label}
    </span>
  );
}

function CountTile({
  count,
  label,
  href,
  tone,
}: {
  count: number;
  label: string;
  href: string;
  tone: "ok" | "error" | "muted";
}) {
  return (
    <Link className={`count-tile count-tile-${tone}`} href={href}>
      <strong>{count.toLocaleString("en-US")}</strong>
      <span>{label}</span>
    </Link>
  );
}

function FeedUrlLabel({ url }: { url: string }) {
  let host: string | null = null;
  let tail = "";
  try {
    const parsed = new URL(url);
    host = parsed.hostname.replace(/^www\./, "");
    tail = parsed.pathname + parsed.search + parsed.hash;
    if (tail === "/") tail = "";
  } catch {
    return <>{url}</>;
  }
  return (
    <>
      <span className="feed-url-host">{host}</span>
      {tail ? <span className="feed-url-path">{tail}</span> : null}
    </>
  );
}

function FeedAction({
  action,
  feedId,
  label,
}: {
  action: (formData: FormData) => Promise<void>;
  feedId: number;
  label: string;
}) {
  return (
    <form action={action} className="feed-action">
      <input name="feed_id" type="hidden" value={feedId} />
      <button className="feed-action-btn" type="submit">
        {label}
      </button>
    </form>
  );
}

function parseFilters(searchParams: PageSearchParams | undefined): ActiveFilters {
  const status = pickStatus(getSingleSearchParam(searchParams, "status"));
  const q = getSingleSearchParam(searchParams, "q")?.trim() || undefined;
  const errors = getSingleSearchParam(searchParams, "errors") === "1";
  return { status, q, errors };
}

function pickStatus(value: string | undefined): ActiveFilters["status"] {
  if (value === "active" || value === "disabled" || value === "removed") {
    return value;
  }
  return "all";
}

function getSingleSearchParam(
  searchParams: PageSearchParams | undefined,
  name: string,
): string | undefined {
  const value = searchParams?.[name];
  return Array.isArray(value) ? value[0] : value;
}
