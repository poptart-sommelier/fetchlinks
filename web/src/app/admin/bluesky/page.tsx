import type { ReactNode } from "react";

import Link from "next/link";

import { formatRelative } from "../../../lib/format-relative";
import { safeExternalHref } from "../../../lib/safe-external-href";
import type { BlueskyFollow } from "../../../models/bluesky-follows";
import { getSqlClient } from "../../../server/sql";
import { getBlueskyFollows } from "../../../server/bluesky";

type LoadResult =
  | {
      status: "ready";
      follows: BlueskyFollow[];
      lastSyncedAt: string | null;
    }
  | { status: "error" };

export const dynamic = "force-dynamic";

export default async function AdminBlueskyPage() {
  const result = await loadFollows();
  return <AdminBlueskyView result={result} />;
}

async function loadFollows(): Promise<LoadResult> {
  try {
    const snapshot = await getBlueskyFollows(getSqlClient(process.env));
    return {
      status: "ready" as const,
      follows: snapshot.follows,
      lastSyncedAt: snapshot.lastSyncedAt,
    };
  } catch {
    return { status: "error" };
  }
}

export function AdminBlueskyView({ result }: { result: LoadResult }) {
  if (result.status === "error") {
    return (
      <main className="shell">
        <header className="page-header">
          <div className="page-title">
            <p className="eyebrow">Fetchlinks admin</p>
            <h1>Bluesky</h1>
          </div>
        </header>
        <section className="state state-error" role="alert">
          <h2>Bluesky follows are unavailable</h2>
          <p>The database could not be opened. Check the server configuration.</p>
        </section>
      </main>
    );
  }

  const { follows, lastSyncedAt } = result;
  const syncedLabel = formatRelative(lastSyncedAt);

  return (
    <main className="shell">
      <header className="page-header">
        <div className="page-title">
          <p className="eyebrow">
            <Link href="/admin">&larr; Admin</Link>
          </p>
          <h1>Bluesky</h1>
        </div>
        <div className="feed-counts-tile" aria-label="Bluesky totals">
          <span className="count-tile count-tile-ok">
            <strong>{follows.length.toLocaleString("en-US")}</strong>
            <span>following</span>
          </span>
        </div>
      </header>

      <p className="admin-readonly-note">
        Read-only mirror of the accounts this Bluesky credential follows. The
        list is refreshed by the ingest job
        {syncedLabel ? (
          <>
            {" "}
            (last synced <strong title={lastSyncedAt ?? undefined}>{syncedLabel}</strong>).
          </>
        ) : (
          <> (not synced yet).</>
        )}
      </p>

      {follows.length === 0 ? (
        <section className="state">
          <h2>No follows</h2>
          <p>The ingest job has not recorded any Bluesky follows yet.</p>
        </section>
      ) : (
        <section className="post-list" aria-label="Bluesky follows">
          {follows.map((follow) => (
            <FollowRow key={follow.did} follow={follow} />
          ))}
        </section>
      )}
    </main>
  );
}

function FollowRow({ follow }: { follow: BlueskyFollow }) {
  return (
    <article className="post-item feed-row feed-row-healthy">
      <header className="feed-row-header">
        <FollowNameLink follow={follow} />
      </header>
      <div className="feed-row-footer">
        <FollowStats follow={follow} />
        <nav aria-label="Bluesky follow actions" className="post-links feed-row-actions">
          {follow.postSource ? <ViewPostsLink source={follow.postSource} /> : null}
        </nav>
      </div>
    </article>
  );
}

function FollowNameLink({ follow }: { follow: BlueskyFollow }) {
  const url = `https://bsky.app/profile/${follow.handle}`;
  const href = safeExternalHref(url);
  const label = (
    <span className="feed-url-host">
      {follow.displayName ? `${follow.displayName} ` : ""}@{follow.handle}
    </span>
  );
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

function FollowStats({ follow }: { follow: BlueskyFollow }) {
  const items: ReactNode[] = [];

  const postLabel = formatRelative(follow.latestPostAt);
  if (postLabel) {
    items.push(
      <span
        key="post"
        className="feed-stat"
        title={follow.latestPostAt ?? undefined}
      >
        Newest post <strong>{postLabel}</strong>
      </span>,
    );
  }

  if (items.length === 0) return null;
  return <div className="feed-stats">{items}</div>;
}

function ViewPostsLink({ source }: { source: string }) {
  return (
    <Link
      aria-label="View posts from this account"
      className="feed-action-btn feed-action-btn-icon feed-action-btn-view"
      href={`/?source=${encodeURIComponent(source)}`}
      title="View posts from this account"
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
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}
