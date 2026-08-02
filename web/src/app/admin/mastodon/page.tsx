import type { ReactNode } from "react";

import Link from "next/link";

import { formatRelative } from "../../../lib/format-relative";
import { safeExternalHref } from "../../../lib/safe-external-href";
import type { MastodonFollow } from "../../../models/mastodon-follows";
import { getSqlClient } from "../../../server/sql";
import { getMastodonFollows } from "../../../server/mastodon";

type LoadResult =
  | {
      status: "ready";
      follows: MastodonFollow[];
      lastSyncedAt: string | null;
    }
  | { status: "error" };

export const dynamic = "force-dynamic";

export default async function AdminMastodonPage() {
  const result = await loadFollows();
  return <AdminMastodonView result={result} />;
}

async function loadFollows(): Promise<LoadResult> {
  try {
    const snapshot = await getMastodonFollows(getSqlClient(process.env));
    return {
      status: "ready" as const,
      follows: snapshot.follows,
      lastSyncedAt: snapshot.lastSyncedAt,
    };
  } catch {
    return { status: "error" };
  }
}

export function AdminMastodonView({ result }: { result: LoadResult }) {
  if (result.status === "error") {
    return (
      <main className="shell">
        <header className="page-header">
          <div className="page-title">
            <p className="eyebrow">Fetchlinks admin</p>
            <h1>Mastodon</h1>
          </div>
        </header>
        <section className="state state-error" role="alert">
          <h2>Mastodon follows are unavailable</h2>
          <p>The database could not be opened. Check the server configuration.</p>
        </section>
      </main>
    );
  }

  const { follows, lastSyncedAt } = result;
  const syncedLabel = formatRelative(lastSyncedAt);
  const groups = groupByInstance(follows);

  return (
    <main className="shell">
      <header className="page-header">
        <div className="page-title">
          <p className="eyebrow">
            <Link href="/admin">&larr; Admin</Link>
          </p>
          <h1>Mastodon</h1>
        </div>
        <div className="feed-counts-tile" aria-label="Mastodon totals">
          <span className="count-tile count-tile-ok">
            <strong>{follows.length.toLocaleString("en-US")}</strong>
            <span>following</span>
          </span>
        </div>
      </header>

      <p className="admin-readonly-note">
        Read-only mirror of the accounts each Mastodon credential follows. The
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
          <p>The ingest job has not recorded any Mastodon follows yet.</p>
        </section>
      ) : (
        groups.map((group) => (
          <section
            key={group.instanceName}
            className="post-list"
            aria-label={`Mastodon follows on ${group.instanceName}`}
          >
            <h2 className="feed-group-heading">{group.instanceName}</h2>
            {group.follows.map((follow) => (
              <FollowRow
                key={`${follow.instanceName}:${follow.accountId}`}
                follow={follow}
              />
            ))}
          </section>
        ))
      )}
    </main>
  );
}

type InstanceGroup = {
  instanceName: string;
  follows: MastodonFollow[];
};

function groupByInstance(follows: MastodonFollow[]): InstanceGroup[] {
  const groups = new Map<string, MastodonFollow[]>();
  for (const follow of follows) {
    const existing = groups.get(follow.instanceName);
    if (existing) {
      existing.push(follow);
    } else {
      groups.set(follow.instanceName, [follow]);
    }
  }
  return Array.from(groups, ([instanceName, instanceFollows]) => ({
    instanceName,
    follows: instanceFollows,
  }));
}

function FollowRow({ follow }: { follow: MastodonFollow }) {
  return (
    <article className="post-item feed-row feed-row-healthy">
      <header className="feed-row-header">
        <FollowNameLink follow={follow} />
      </header>
      <div className="feed-row-footer">
        <FollowStats follow={follow} />
        <nav aria-label="Mastodon follow actions" className="post-links feed-row-actions">
          {follow.postSource ? <ViewPostsLink source={follow.postSource} /> : null}
        </nav>
      </div>
    </article>
  );
}

function FollowNameLink({ follow }: { follow: MastodonFollow }) {
  const url = follow.url ?? "";
  const href = url ? safeExternalHref(url) : null;
  const label = (
    <span className="feed-url-host">
      {follow.displayName ? `${follow.displayName} ` : ""}@{follow.acct}
    </span>
  );
  if (!href) {
    return (
      <span className="post-source feed-row-url" title={url || undefined}>
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

function FollowStats({ follow }: { follow: MastodonFollow }) {
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
