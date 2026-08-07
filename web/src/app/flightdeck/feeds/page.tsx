import type { ReactNode } from "react";

import Link from "next/link";

import { formatRelative } from "../../../lib/format-relative";
import { safeExternalHref } from "../../../lib/safe-external-href";
import type { RssFeed, RssFeedStatus } from "../../../models/rss-feeds";
import {
  countRssFeedsByStatus,
  listRssFeedsPage,
  type RssFeedCounts,
  type RssFeedPage,
} from "../../../server/feeds";
import { getSqlClient } from "../../../server/sql";
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
  | {
      status: "ready";
      page: RssFeedPage;
      counts: RssFeedCounts;
      filters: ActiveFilters;
      addFeedback: AddFeedback | null;
      confirmRemoveId: number | null;
    }
  | { status: "error" };

type ActiveFilters = {
  status: RssFeedStatus | "all";
  q?: string;
  errors: boolean;
  page: number;
};

type AddFeedback =
  | { kind: "ok"; url: string }
  | { kind: "exists"; url: string }
  | { kind: "invalid"; reason: string; url?: string };

export const dynamic = "force-dynamic";

// Matches the public list. 719 feeds in one response is 2.3 MB of HTML and
// slow enough to time out an in-browser evaluation.
const FEEDS_PER_PAGE = 50;

export default async function AdminFeedsPage({
  searchParams,
}: AdminFeedsPageProps = {}) {
  const resolved = await searchParams;
  const filters = parseFilters(resolved);
  const addFeedback = parseAddFeedback(resolved);
  const confirmRemoveId = parseConfirmRemoveId(resolved);
  const result = await loadFeeds(filters, addFeedback, confirmRemoveId);
  return <AdminFeedsView result={result} />;
}

async function loadFeeds(
  filters: ActiveFilters,
  addFeedback: AddFeedback | null,
  confirmRemoveId: number | null,
): Promise<LoadResult> {
  try {
    const sql = getSqlClient(process.env);
    return {
      status: "ready" as const,
      page: await listRssFeedsPage(sql, filters, filters.page, FEEDS_PER_PAGE),
      counts: await countRssFeedsByStatus(sql),
      filters,
      addFeedback,
      confirmRemoveId,
    };
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

  const { page, counts, filters, addFeedback, confirmRemoveId } = result;
  const feeds = page.feeds;

  return (
    <main className="shell">
      <header className="page-header">
        <div className="page-title">
          <p className="eyebrow">
            <Link href="/flightdeck">&larr; Admin</Link>
          </p>
          <h1>RSS feeds</h1>
        </div>
        <div className="feed-counts-tile" aria-label="Feed totals">
          <CountTile
            count={counts.active}
            label={counts.active === 1 ? "active" : "active"}
            href="/flightdeck/feeds?status=active"
            tone="ok"
          />
          <CountTile
            count={counts.errors}
            label={counts.errors === 1 ? "error" : "errors"}
            href="/flightdeck/feeds?errors=1"
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

      {addFeedback ? <AddFeedbackBanner feedback={addFeedback} /> : null}

      <form action="/flightdeck/feeds" aria-label="Search feeds" className="search-form" method="get">
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
          <Link className="clear-filters" href="/flightdeck/feeds">
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
        <>
          <section className="post-list" aria-label="RSS feeds">
            {feeds.map((feed) => (
              <FeedRow
                key={feed.id}
                feed={feed}
                filters={filters}
                confirmRemove={confirmRemoveId === feed.id}
              />
            ))}
          </section>
          <FeedsPagination filters={filters} page={page} />
        </>
      )}
    </main>
  );
}

function FeedsPagination({
  filters,
  page,
}: {
  filters: ActiveFilters;
  page: RssFeedPage;
}) {
  if (page.totalPages <= 1) return null;

  return (
    <nav className="pagination" aria-label="Feeds pagination">
      {page.hasPreviousPage ? (
        <Link href={buildFeedsHref({ ...filters, page: page.page - 1 })}>
          Previous
        </Link>
      ) : (
        <span aria-disabled="true">Previous</span>
      )}
      <span>
        Page {page.page.toLocaleString("en-US")} of{" "}
        {page.totalPages.toLocaleString("en-US")}
      </span>
      {page.hasNextPage ? (
        <Link href={buildFeedsHref({ ...filters, page: page.page + 1 })}>
          Next
        </Link>
      ) : (
        <span aria-disabled="true">Next</span>
      )}
    </nav>
  );
}

function FeedRow({
  feed,
  filters,
  confirmRemove,
}: {
  feed: RssFeed;
  filters: ActiveFilters;
  confirmRemove: boolean;
}) {
  const health = getFeedHealth(feed);
  const rowClass = `post-item feed-row feed-row-${health}${
    confirmRemove ? " feed-row-confirming" : ""
  }`;
  return (
    <article className={rowClass}>
      <header className="feed-row-header">
        <FeedUrlLink feed={feed} />
        <FeedStatusPill feed={feed} />
      </header>
      <div className="feed-row-footer">
        <FeedStats feed={feed} />
        <nav aria-label="Feed actions" className="feed-row-actions">
          {feed.status !== "removed" && !confirmRemove ? (
            <ViewPostsControl siteLink={feed.siteLink} />
          ) : null}
          {feed.status !== "removed" ? (
            confirmRemove ? (
              <ConfirmRemove feedId={feed.id} filters={filters} />
            ) : (
              <Link
                className="feed-action-btn feed-action-btn-remove"
                href={buildFeedsHref(filters, { confirm_remove: String(feed.id) })}
                title="Remove feed"
              >
                <TrashIcon />
                Remove feed
              </Link>
            )
          ) : null}
          {feed.status === "removed" ? (
            <FeedAction action={restoreFeedAction} feedId={feed.id} label="Restore" />
          ) : null}
        </nav>
      </div>
    </article>
  );
}

function ConfirmRemove({
  feedId,
  filters,
}: {
  feedId: number;
  filters: ActiveFilters;
}) {
  return (
    <span className="feed-confirm" role="group" aria-label="Confirm remove">
      <span className="feed-confirm-prompt">Remove this feed?</span>
      <form action={deleteFeedAction} className="feed-action">
        <input name="feed_id" type="hidden" value={feedId} />
        <button className="feed-action-btn feed-action-btn-danger" type="submit">
          Remove
        </button>
      </form>
      <Link className="feed-action-btn feed-action-btn-ghost" href={buildFeedsHref(filters)}>
        Cancel
      </Link>
    </span>
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

  if (feed.consecutiveFailures > 0 || (feed.lastStatus !== null && feed.lastStatus >= 400)) {
    const failureLabel =
      feed.consecutiveFailures > 0
        ? `${feed.consecutiveFailures} consecutive ${
            feed.consecutiveFailures === 1 ? "failure" : "failures"
          }`
        : `HTTP ${feed.lastStatus}`;
    const tip = buildUnhealthyTip(feed);
    items.push(
      <span
        key="fail"
        className="feed-stat feed-stat-danger feed-stat-has-tip"
        tabIndex={tip ? 0 : undefined}
        title={tip}
      >
        {failureLabel}
        {tip ? (
          <span className="feed-stat-tip" role="tooltip">
            {tip}
          </span>
        ) : null}
      </span>,
    );
  }

  // Always render the container, even when empty: the footer relies on having
  // exactly two children to keep the actions pinned right, and a row with no
  // stats otherwise collapses to a different shape than its neighbours.
  if (items.length === 0) {
    items.push(
      <span key="never" className="feed-stat">
        Never fetched
      </span>,
    );
  }
  return <div className="feed-stats">{items}</div>;
}

type FeedHealth = "healthy" | "unhealthy" | "removed";

const HEALTH_PILL_LABEL: Record<FeedHealth, string | null> = {
  healthy: null,
  unhealthy: null,
  removed: "Removed",
};

function getFeedHealth(feed: RssFeed): FeedHealth {
  if (feed.status === "removed") return "removed";
  if (feed.consecutiveFailures > 0) return "unhealthy";
  if (feed.lastStatus !== null && feed.lastStatus >= 400) return "unhealthy";
  return "healthy";
}

function buildUnhealthyTip(feed: RssFeed): string | undefined {
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
  return (
    <span className={`status-pill status-pill-${health}`}>
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

function FeedUrlLink({ feed }: { feed: RssFeed }) {
  const href = safeExternalHref(feed.feedUrl);
  if (!href) {
    return (
      <span className="post-source feed-row-url" title={feed.feedUrl}>
        <FeedUrlLabel url={feed.feedUrl} />
      </span>
    );
  }
  return (
    <a
      className="post-source feed-row-url"
      href={href}
      rel="noreferrer"
      target="_blank"
      title={feed.feedUrl}
    >
      <FeedUrlLabel url={feed.feedUrl} />
    </a>
  );
}

function FeedAction({
  action,
  feedId,
  label,
  icon,
  iconOnly = false,
}: {
  action: (formData: FormData) => Promise<void>;
  feedId: number;
  label: string;
  icon?: ReactNode;
  iconOnly?: boolean;
}) {
  const className = `feed-action-btn${iconOnly ? " feed-action-btn-icon" : ""}`;
  return (
    <form action={action} className="feed-action">
      <input name="feed_id" type="hidden" value={feedId} />
      <button
        aria-label={iconOnly ? label : undefined}
        className={className}
        title={iconOnly ? label : undefined}
        type="submit"
      >
        {icon}
        {iconOnly ? null : label}
      </button>
    </form>
  );
}

function TrashIcon() {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      focusable="false"
      height="16"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.75"
      viewBox="0 0 24 24"
      width="16"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

// The primary action on this page: the reason to look at a feed is almost
// always to see what it produced. A feed only gains a site link after a
// successful fetch, and 47 of 719 have none, so the control is shown disabled
// with the reason rather than omitted -- a button that vanishes on some rows
// reads as a bug.
function ViewPostsControl({ siteLink }: { siteLink: string | null }) {
  if (!siteLink) {
    return (
      <span
        aria-disabled="true"
        className="feed-action-btn feed-action-btn-view feed-action-btn-disabled"
        title="No site link recorded yet. It is read from the feed on the first successful fetch, and posts cannot be filtered without it."
      >
        <ViewPostsIcon />
        View posts
      </span>
    );
  }
  return (
    <Link
      className="feed-action-btn feed-action-btn-view"
      href={`/?source=${encodeURIComponent(siteLink)}`}
      title={`Show posts collected from ${siteLink}`}
    >
      <ViewPostsIcon />
      View posts
    </Link>
  );
}

function ViewPostsIcon() {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      focusable="false"
      height="16"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.75"
      viewBox="0 0 24 24"
      width="16"
    >
      <path d="M4 5h6" />
      <path d="M4 12h6" />
      <path d="M4 19h10" />
      <path d="M15 9l5 5-5 5" />
      <path d="M20 14h-9" />
    </svg>
  );
}

function parseFilters(searchParams: PageSearchParams | undefined): ActiveFilters {
  const status = pickStatus(getSingleSearchParam(searchParams, "status"));
  const q = getSingleSearchParam(searchParams, "q")?.trim().slice(0, 200) || undefined;
  const errors = getSingleSearchParam(searchParams, "errors") === "1";
  const page = parsePage(getSingleSearchParam(searchParams, "page"));
  return { status, q, errors, page };
}

function parsePage(raw: string | undefined): number {
  if (!raw) return 1;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1;
}

function parseAddFeedback(
  searchParams: PageSearchParams | undefined,
): AddFeedback | null {
  const added = getSingleSearchParam(searchParams, "added");
  const url = getSingleSearchParam(searchParams, "url");
  if (added === "ok" && url) return { kind: "ok", url };
  if (added === "exists" && url) return { kind: "exists", url };
  if (added === "invalid") {
    const reason =
      getSingleSearchParam(searchParams, "reason") || "Could not add feed.";
    return { kind: "invalid", reason, url };
  }
  return null;
}

function parseConfirmRemoveId(
  searchParams: PageSearchParams | undefined,
): number | null {
  const raw = getSingleSearchParam(searchParams, "confirm_remove");
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : null;
}

function buildFeedsHref(
  filters: ActiveFilters,
  extra: Record<string, string> = {},
): string {
  const params = new URLSearchParams();
  if (filters.status !== "all") params.set("status", filters.status);
  if (filters.q) params.set("q", filters.q);
  if (filters.errors) params.set("errors", "1");
  // Page 1 is the default, so it stays out of the URL and the plain
  // /flightdeck/feeds link keeps working as "start again".
  if (filters.page > 1) params.set("page", String(filters.page));
  for (const [key, value] of Object.entries(extra)) {
    params.set(key, value);
  }
  const query = params.toString();
  return query ? `/flightdeck/feeds?${query}` : "/flightdeck/feeds";
}

function AddFeedbackBanner({ feedback }: { feedback: AddFeedback }) {
  if (feedback.kind === "ok") {
    return (
      <p className="add-feedback add-feedback-ok" role="status">
        <span className="add-feedback-label">Added</span>
        <span className="add-feedback-body">
          <code>{feedback.url}</code> is now being fetched.
        </span>
      </p>
    );
  }
  if (feedback.kind === "exists") {
    return (
      <p className="add-feedback add-feedback-warn" role="status">
        <span className="add-feedback-label">Already added</span>
        <span className="add-feedback-body">
          <code>{feedback.url}</code> is already in the list.
        </span>
      </p>
    );
  }
  return (
    <p className="add-feedback add-feedback-error" role="alert">
      <span className="add-feedback-label">Not added</span>
      <span className="add-feedback-body">
        {feedback.reason}
        {feedback.url ? (
          <>
            {" "}
            <code>{feedback.url}</code>
          </>
        ) : null}
      </span>
    </p>
  );
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
