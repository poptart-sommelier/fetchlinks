import type { ReactNode } from "react";

import Link from "next/link";

import { formatRelative } from "../../../lib/format-relative";
import { safeExternalHref } from "../../../lib/safe-external-href";
import type { Subreddit, SubredditStatus } from "../../../models/subreddits";
import {
  countSubredditsByStatus,
  listSubreddits,
  withWritableSubredditsDatabase,
  type SubredditCounts,
} from "../../../server/subreddits";
import { loadAppConfig } from "../../../server/config";
import {
  addSubredditAction,
  deleteSubredditAction,
  restoreSubredditAction,
} from "./actions";

type PageSearchParams = Record<string, string | string[] | undefined>;

type AdminRedditPageProps = {
  searchParams?: Promise<PageSearchParams>;
};

type LoadResult =
  | {
      status: "ready";
      subreddits: Subreddit[];
      counts: SubredditCounts;
      filters: ActiveFilters;
      addFeedback: AddFeedback | null;
      confirmRemoveId: number | null;
    }
  | { status: "error" };

type ActiveFilters = {
  status: SubredditStatus | "all";
  q?: string;
};

type AddFeedback =
  | { kind: "ok"; name: string }
  | { kind: "exists"; name: string }
  | { kind: "invalid"; reason: string; name?: string };

export const dynamic = "force-dynamic";

export default async function AdminRedditPage({
  searchParams,
}: AdminRedditPageProps = {}) {
  const resolved = await searchParams;
  const filters = parseFilters(resolved);
  const addFeedback = parseAddFeedback(resolved);
  const confirmRemoveId = parseConfirmRemoveId(resolved);
  const result = loadSubreddits(filters, addFeedback, confirmRemoveId);
  return <AdminRedditView result={result} />;
}

function loadSubreddits(
  filters: ActiveFilters,
  addFeedback: AddFeedback | null,
  confirmRemoveId: number | null,
): LoadResult {
  try {
    const config = loadAppConfig(process.env);
    return withWritableSubredditsDatabase(config, (db) => ({
      status: "ready" as const,
      subreddits: listSubreddits(db, filters),
      counts: countSubredditsByStatus(db),
      filters,
      addFeedback,
      confirmRemoveId,
    }));
  } catch {
    return { status: "error" };
  }
}

export function AdminRedditView({ result }: { result: LoadResult }) {
  if (result.status === "error") {
    return (
      <main className="shell">
        <header className="page-header">
          <div className="page-title">
            <p className="eyebrow">Fetchlinks admin</p>
            <h1>Subreddits</h1>
          </div>
        </header>
        <section className="state state-error" role="alert">
          <h2>Subreddits are unavailable</h2>
          <p>The database could not be opened. Check the server configuration.</p>
        </section>
      </main>
    );
  }

  const { subreddits, counts, filters, addFeedback, confirmRemoveId } = result;

  return (
    <main className="shell">
      <header className="page-header">
        <div className="page-title">
          <p className="eyebrow">
            <Link href="/admin">&larr; Admin</Link>
          </p>
          <h1>Subreddits</h1>
        </div>
        <div className="feed-counts-tile" aria-label="Subreddit totals">
          <CountTile
            count={counts.active}
            label="active"
            href="/admin/reddit?status=active"
            tone="ok"
          />
          <CountTile
            count={counts.removed}
            label={counts.removed === 1 ? "removed" : "removed"}
            href="/admin/reddit?status=removed"
            tone="muted"
          />
        </div>
      </header>

      <form action={addSubredditAction} aria-label="Add subreddit" className="add-form">
        <label className="add-form-field">
          <span>Add subreddit</span>
          <input
            name="subreddit_name"
            placeholder="r/netsec"
            required
            type="text"
          />
        </label>
        <button className="add-form-btn" type="submit">
          Add subreddit
        </button>
      </form>

      {addFeedback ? <AddFeedbackBanner feedback={addFeedback} /> : null}

      <form action="/admin/reddit" aria-label="Search subreddits" className="search-form" method="get">
        <label className="search-form-field">
          <span>Search</span>
          <input
            defaultValue={filters.q ?? ""}
            name="q"
            placeholder="Subreddit name substring"
            type="search"
          />
        </label>
        {filters.status !== "all" ? (
          <input name="status" type="hidden" value={filters.status} />
        ) : null}
        <button className="search-form-btn" type="submit">
          Search
        </button>
        {filters.status !== "all" || filters.q ? (
          <Link className="clear-filters" href="/admin/reddit">
            Clear
          </Link>
        ) : null}
      </form>

      {subreddits.length === 0 ? (
        <section className="state">
          <h2>No subreddits</h2>
          <p>No rows match the current filters.</p>
        </section>
      ) : (
        <section className="post-list" aria-label="Subreddits">
          {subreddits.map((subreddit) => (
            <SubredditRow
              key={subreddit.id}
              subreddit={subreddit}
              filters={filters}
              confirmRemove={confirmRemoveId === subreddit.id}
            />
          ))}
        </section>
      )}
    </main>
  );
}

function SubredditRow({
  subreddit,
  filters,
  confirmRemove,
}: {
  subreddit: Subreddit;
  filters: ActiveFilters;
  confirmRemove: boolean;
}) {
  const health = subreddit.status === "removed" ? "removed" : "healthy";
  const rowClass = `post-item feed-row feed-row-${health}${
    confirmRemove ? " feed-row-confirming" : ""
  }`;
  return (
    <article className={rowClass}>
      <header className="feed-row-header">
        <SubredditNameLink subreddit={subreddit} />
        <SubredditStatusPill subreddit={subreddit} />
      </header>
      <div className="feed-row-footer">
        <SubredditStats subreddit={subreddit} />
        <nav aria-label="Subreddit actions" className="post-links feed-row-actions">
          {subreddit.status !== "removed" && subreddit.postSource && !confirmRemove ? (
            <ViewPostsLink source={subreddit.postSource} />
          ) : null}
          {subreddit.status !== "removed" ? (
            confirmRemove ? (
              <ConfirmRemove subredditId={subreddit.id} filters={filters} />
            ) : (
              <Link
                aria-label="Remove subreddit"
                className="feed-action-btn feed-action-btn-icon"
                href={buildRedditHref(filters, {
                  confirm_remove: String(subreddit.id),
                })}
                title="Remove subreddit"
              >
                <TrashIcon />
              </Link>
            )
          ) : null}
          {subreddit.status === "removed" ? (
            <SubredditAction
              action={restoreSubredditAction}
              subredditId={subreddit.id}
              label="Restore"
            />
          ) : null}
        </nav>
      </div>
    </article>
  );
}

function ConfirmRemove({
  subredditId,
  filters,
}: {
  subredditId: number;
  filters: ActiveFilters;
}) {
  return (
    <span className="feed-confirm" role="group" aria-label="Confirm remove">
      <span className="feed-confirm-prompt">Remove this subreddit?</span>
      <form action={deleteSubredditAction} className="feed-action">
        <input name="subreddit_id" type="hidden" value={subredditId} />
        <button className="feed-action-btn feed-action-btn-danger" type="submit">
          Remove
        </button>
      </form>
      <Link className="feed-action-btn feed-action-btn-ghost" href={buildRedditHref(filters)}>
        Cancel
      </Link>
    </span>
  );
}

function SubredditStats({ subreddit }: { subreddit: Subreddit }) {
  const items: ReactNode[] = [];

  const fetchedLabel = formatRelative(subreddit.lastFetchedAt);
  if (fetchedLabel) {
    items.push(
      <span
        key="fetched"
        className="feed-stat"
        title={subreddit.lastFetchedAt ?? undefined}
      >
        Fetched <strong>{fetchedLabel}</strong>
      </span>,
    );
  }

  const postLabel = formatRelative(subreddit.latestPostAt);
  if (postLabel) {
    items.push(
      <span
        key="post"
        className="feed-stat"
        title={subreddit.latestPostAt ?? undefined}
      >
        Newest post <strong>{postLabel}</strong>
      </span>,
    );
  }

  if (items.length === 0) return null;
  return <div className="feed-stats">{items}</div>;
}

export function SubredditStatusPill({ subreddit }: { subreddit: Subreddit }) {
  if (subreddit.status !== "removed") return null;
  return <span className="status-pill status-pill-removed">Removed</span>;
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

function SubredditNameLink({ subreddit }: { subreddit: Subreddit }) {
  const url = `https://www.reddit.com/r/${subreddit.name}`;
  const href = safeExternalHref(url);
  const label = <span className="feed-url-host">r/{subreddit.name}</span>;
  if (!href) {
    return (
      <span className="post-source feed-row-url" title={url}>
        {label}
      </span>
    );
  }
  return (
    <a
      className="post-source feed-row-url"
      href={href}
      rel="noreferrer"
      target="_blank"
      title={url}
    >
      {label}
    </a>
  );
}

function SubredditAction({
  action,
  subredditId,
  label,
  icon,
  iconOnly = false,
}: {
  action: (formData: FormData) => Promise<void>;
  subredditId: number;
  label: string;
  icon?: ReactNode;
  iconOnly?: boolean;
}) {
  const className = `feed-action-btn${iconOnly ? " feed-action-btn-icon" : ""}`;
  return (
    <form action={action} className="feed-action">
      <input name="subreddit_id" type="hidden" value={subredditId} />
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

function ViewPostsLink({ source }: { source: string }) {
  return (
    <Link
      aria-label="View posts from this subreddit"
      className="feed-action-btn feed-action-btn-icon feed-action-btn-view"
      href={`/?source=${encodeURIComponent(source)}`}
      title="View posts from this subreddit"
    >
      <ViewPostsIcon />
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
  return { status, q };
}

function parseAddFeedback(
  searchParams: PageSearchParams | undefined,
): AddFeedback | null {
  const added = getSingleSearchParam(searchParams, "added");
  const name = getSingleSearchParam(searchParams, "name");
  if (added === "ok" && name) return { kind: "ok", name };
  if (added === "exists" && name) return { kind: "exists", name };
  if (added === "invalid") {
    const reason =
      getSingleSearchParam(searchParams, "reason") || "Could not add subreddit.";
    return { kind: "invalid", reason, name };
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

function buildRedditHref(
  filters: ActiveFilters,
  extra: Record<string, string> = {},
): string {
  const params = new URLSearchParams();
  if (filters.status !== "all") params.set("status", filters.status);
  if (filters.q) params.set("q", filters.q);
  for (const [key, value] of Object.entries(extra)) {
    params.set(key, value);
  }
  const query = params.toString();
  return query ? `/admin/reddit?${query}` : "/admin/reddit";
}

function AddFeedbackBanner({ feedback }: { feedback: AddFeedback }) {
  if (feedback.kind === "ok") {
    return (
      <p className="add-feedback add-feedback-ok" role="status">
        <span className="add-feedback-label">Added</span>
        <span className="add-feedback-body">
          <code>r/{feedback.name}</code> is now being fetched.
        </span>
      </p>
    );
  }
  if (feedback.kind === "exists") {
    return (
      <p className="add-feedback add-feedback-warn" role="status">
        <span className="add-feedback-label">Already added</span>
        <span className="add-feedback-body">
          <code>r/{feedback.name}</code> is already in the list.
        </span>
      </p>
    );
  }
  return (
    <p className="add-feedback add-feedback-error" role="alert">
      <span className="add-feedback-label">Not added</span>
      <span className="add-feedback-body">
        {feedback.reason}
        {feedback.name ? (
          <>
            {" "}
            <code>{feedback.name}</code>
          </>
        ) : null}
      </span>
    </p>
  );
}

function pickStatus(value: string | undefined): SubredditStatus | "all" {
  if (value === "active" || value === "disabled" || value === "removed") {
    return value;
  }
  return "all";
}

function getSingleSearchParam(
  searchParams: PageSearchParams | undefined,
  key: string,
): string | undefined {
  const value = searchParams?.[key];
  if (Array.isArray(value)) return value[0];
  return value;
}
