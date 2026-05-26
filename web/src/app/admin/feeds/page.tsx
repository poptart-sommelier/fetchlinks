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
};

export const dynamic = "force-dynamic";

const STATUS_OPTIONS: { value: ActiveFilters["status"]; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "disabled", label: "Disabled" },
  { value: "removed", label: "Removed" },
];

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
            <Link href="/">&larr; Fetchlinks</Link> &middot; admin
          </p>
          <h1>RSS feeds</h1>
        </div>
        <p className="page-summary">
          <CountLink count={counts.active} label="active" status="active" />
          {" / "}
          <CountLink count={counts.disabled} label="disabled" status="disabled" />
          {" / "}
          <CountLink count={counts.removed} label="removed" status="removed" />
        </p>
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

      <form action="/admin/feeds" aria-label="Filter feeds" className="filter-bar" method="get">
        <label>
          <span>Status</span>
          <select defaultValue={filters.status} name="status">
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Search</span>
          <input
            defaultValue={filters.q ?? ""}
            name="q"
            placeholder="Feed URL substring"
            type="search"
          />
        </label>
        <div className="filter-actions">
          <button type="submit">Apply</button>
          {filters.status !== "all" || filters.q ? (
            <Link className="clear-filters" href="/admin/feeds">
              Clear
            </Link>
          ) : null}
        </div>
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
        <FeedStatusPill status={feed.status} />
      </header>
      <FeedStats feed={feed} />
      {feed.lastError ? (
        <p className="feed-error" role="status">
          <span className="feed-error-label">Last error</span>
          <span className="feed-error-body">{feed.lastError}</span>
        </p>
      ) : null}
      <nav aria-label="Feed actions" className="post-links">
        {feed.status !== "removed" ? (
          <FeedAction
            action={deleteFeedAction}
            feedId={feed.id}
            label="remove"
            tone="danger"
          />
        ) : null}
        {feed.status === "removed" ? (
          <FeedAction action={restoreFeedAction} feedId={feed.id} label="restore" />
        ) : null}
      </nav>
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

  if (feed.lastStatus !== null) {
    const tone = feed.lastStatus >= 400 ? "danger" : "ok";
    items.push(
      <span key="status" className={`feed-stat feed-stat-${tone}`}>
        HTTP <strong>{feed.lastStatus}</strong>
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

const STATUS_PILL_LABEL: Record<RssFeedStatus, string> = {
  active: "Active",
  disabled: "Disabled",
  removed: "Removed",
};

export function FeedStatusPill({ status }: { status: RssFeedStatus }) {
  return (
    <span className={`status-pill status-pill-${status}`}>
      {STATUS_PILL_LABEL[status]}
    </span>
  );
}

function CountLink({
  count,
  label,
  status,
}: {
  count: number;
  label: string;
  status: RssFeedStatus;
}) {
  return (
    <Link className="count-link" href={`/admin/feeds?status=${status}`}>
      <strong>{count.toLocaleString("en-US")}</strong> <span>{label}</span>
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
  tone = "default",
}: {
  action: (formData: FormData) => Promise<void>;
  feedId: number;
  label: string;
  tone?: "default" | "danger";
}) {
  const toneClass = tone === "danger" ? " feed-action-btn-danger" : "";
  return (
    <form action={action} className="feed-action">
      <input name="feed_id" type="hidden" value={feedId} />
      <button className={`feed-action-btn${toneClass}`} type="submit">
        {label}
      </button>
    </form>
  );
}

function parseFilters(searchParams: PageSearchParams | undefined): ActiveFilters {
  const status = pickStatus(getSingleSearchParam(searchParams, "status"));
  const q = getSingleSearchParam(searchParams, "q")?.trim() || undefined;
  return { status, q };
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
