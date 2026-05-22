import Link from "next/link";

import type { RssFeed, RssFeedStatus } from "../../../models/rss-feeds";
import {
  countRssFeedsByStatus,
  listRssFeeds,
  withWritableFetchlinksDatabase,
  type RssFeedCounts,
} from "../../../server/feeds";
import { loadAppConfig } from "../../../server/config";
import {
  addFeedAction,
  deleteFeedAction,
  disableFeedAction,
  enableFeedAction,
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
          <strong>{counts.active.toLocaleString("en-US")}</strong>{" "}
          <span>active</span>
          {" / "}
          <strong>{counts.disabled.toLocaleString("en-US")}</strong>{" "}
          <span>disabled</span>
          {" / "}
          <strong>{counts.removed.toLocaleString("en-US")}</strong>{" "}
          <span>removed</span>
        </p>
      </header>

      <section aria-label="Add feed" className="filter-bar">
        <form action={addFeedAction} className="filter-bar">
          <label>
            <span>Add feed</span>
            <input
              name="feed_url"
              placeholder="https://example.com/feed.xml"
              required
              type="url"
            />
          </label>
          <div className="filter-actions">
            <button type="submit">Add</button>
          </div>
        </form>
      </section>

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
    <article className="post-item">
      <header className="post-heading">
        <div className="post-meta">
          <a
            className="post-source"
            href={feed.feedUrl}
            rel="noreferrer"
            target="_blank"
            title={feed.feedUrl}
          >
            {feed.feedUrl}
          </a>
          <span aria-hidden="true" className="post-meta-separator">/</span>
          <span>{feed.status}</span>
          {feed.consecutiveFailures > 0 ? (
            <>
              <span aria-hidden="true" className="post-meta-separator">/</span>
              <span title={feed.lastError ?? ""}>
                failures={feed.consecutiveFailures}
              </span>
            </>
          ) : null}
          {feed.lastSuccessAt ? (
            <>
              <span aria-hidden="true" className="post-meta-separator">/</span>
              <span>last success {feed.lastSuccessAt}</span>
            </>
          ) : null}
        </div>
      </header>
      {feed.lastError ? <p>{feed.lastError}</p> : null}
      <nav aria-label="Feed actions" className="post-links">
        {feed.status === "active" ? (
          <FeedAction action={disableFeedAction} feedId={feed.id} label="disable" />
        ) : null}
        {feed.status === "disabled" ? (
          <FeedAction action={enableFeedAction} feedId={feed.id} label="enable" />
        ) : null}
        {feed.status !== "removed" ? (
          <FeedAction action={deleteFeedAction} feedId={feed.id} label="remove" />
        ) : null}
        {feed.status === "removed" ? (
          <FeedAction action={restoreFeedAction} feedId={feed.id} label="restore" />
        ) : null}
      </nav>
    </article>
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
    <form action={action} className="post-link-action" style={{ display: "inline" }}>
      <input name="feed_id" type="hidden" value={feedId} />
      <button type="submit">{label}</button>
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
