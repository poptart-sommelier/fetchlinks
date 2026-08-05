import Link from "next/link";
import { Fragment } from "react";

import { formatRelative } from "../lib/format-relative";
import { safeExternalHref } from "../lib/safe-external-href";
import type {
  PostPage,
  PostSummary,
  PostUrl,
  SourceType,
} from "../models/read-models";
import { getPosts, type PostFilters } from "../server/db";
import { getSqlClient } from "../server/sql";

type PageSearchParams = Record<string, string | string[] | undefined>;

type HomeProps = {
  searchParams?: Promise<PageSearchParams>;
};

type Env = Partial<Record<string, string | undefined>>;

type ActiveFilters = {
  source?: string;
  sourceType?: SourceType;
  author?: string;
  q?: string;
};

type LatestPostsResult =
  | {
      status: "ready";
      page: PostPage;
      filters: ActiveFilters;
    }
  | {
      status: "error";
    };

const POSTS_PER_PAGE = 50;

const VALID_SOURCE_TYPES: readonly SourceType[] = [
  "rss",
  "reddit",
  "bluesky",
  "mastodon",
];

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "UTC",
});

export const dynamic = "force-dynamic";

export default async function Home({ searchParams }: HomeProps = {}) {
  const resolvedSearchParams = await searchParams;
  const page = getPageFromSearchParams(resolvedSearchParams);
  const filters = getFiltersFromSearchParams(resolvedSearchParams);

  return <LatestPostsView result={await loadLatestPosts({ page, filters })} />;
}

export async function loadLatestPosts({
  env = process.env,
  filters = {},
  page = 1,
}: {
  env?: Env;
  filters?: PostFilters;
  page?: number;
} = {}): Promise<LatestPostsResult> {
  const activeFilters = normalizeFilters(filters);

  try {
    const sql = getSqlClient(env);
    const requested = await getPosts(sql, {
      ...activeFilters,
      page,
      pageSize: POSTS_PER_PAGE,
    });

    // A hand-typed ?page= past the end otherwise renders "Page 999 of 50" over
    // an empty list, with a Previous link into more emptiness. Land on the last
    // real page instead. The second query only happens on that dead path.
    const lastPage = Math.max(requested.totalPages, 1);
    const resolved =
      page > lastPage && requested.totalPosts > 0
        ? await getPosts(sql, {
            ...activeFilters,
            page: lastPage,
            pageSize: POSTS_PER_PAGE,
          })
        : requested;

    return {
      status: "ready",
      page: resolved,
      filters: activeFilters,
    };
  } catch {
    return { status: "error" };
  }
}

export function LatestPostsView({ result }: { result: LatestPostsResult }) {
  if (result.status === "error") {
    return (
      <main className="shell shell-reading">
        <PageHeader />
        <section className="state state-error" role="alert">
          <h2>Posts are unavailable</h2>
          <p>The database could not be opened. Check the server configuration.</p>
        </section>
      </main>
    );
  }

  const { page } = result;

  return (
    <main className="shell shell-reading">
      <PageHeader filters={result.filters} page={page} />
      <FilterBar filters={result.filters} />
      {page.posts.length === 0 ? (
        <EmptyPostsState filters={result.filters} page={page} />
      ) : null}
      {page.posts.length > 0 ? (
        <section className="post-list" aria-label="Latest posts">
          {page.posts.map((post) => (
            <PostListItem
              key={post.id}
              filters={result.filters}
              post={post}
            />
          ))}
        </section>
      ) : null}
      <Pagination filters={result.filters} page={page} />
    </main>
  );
}

function PageHeader({
  filters = {},
  page,
}: {
  filters?: ActiveFilters;
  page?: PostPage;
}) {
  const summaryLabel = hasActiveFilters(filters)
    ? "matching posts"
    : "posts collected";

  return (
    <header className="page-header">
      <div className="page-title">
        <p className="eyebrow">
          <Link href="/">Fetchlinks</Link>
        </p>
        <h1>{describeFilters(filters) ?? "Latest posts"}</h1>
      </div>
      {page ? (
        <p
          aria-label={`${page.totalPosts.toLocaleString("en-US")} ${summaryLabel}`}
          className="page-summary"
        >
          <strong>{page.totalPosts.toLocaleString("en-US")}</strong>{" "}
          <span>{summaryLabel}</span>
        </p>
      ) : null}
    </header>
  );
}

/**
 * What the reader is currently looking at, for the page heading. Without this
 * a filtered view still announces itself as "Latest posts" and the only clue
 * that a filter is applied is the match count.
 */
function describeFilters(filters: ActiveFilters): string | undefined {
  if (filters.author) {
    return filters.author;
  }

  if (filters.source) {
    return formatUrlLabel(filters.source);
  }

  if (filters.sourceType) {
    return filters.sourceType;
  }

  return filters.q ? `Results for “${filters.q}”` : undefined;
}

function FilterBar({ filters }: { filters: ActiveFilters }) {
  return (
    <form action="/" aria-label="Filter posts" className="filter-bar" method="get">
      <label className="filter-search">
        <span>Search</span>
        <input
          defaultValue={filters.q ?? ""}
          name="q"
          placeholder="Description, author, URL"
          type="search"
        />
      </label>
      <div className="filter-actions">
        <button type="submit">Search</button>
        {hasActiveFilters(filters) ? (
          <Link className="clear-filters" href="/">
            Clear
          </Link>
        ) : null}
      </div>
    </form>
  );
}

function EmptyPostsState({ filters, page }: { filters: ActiveFilters; page: PostPage }) {
  const message =
    page.totalPosts === 0 && hasActiveFilters(filters)
      ? "No posts match the current filters."
      : page.totalPosts === 0
      ? "No posts have been collected yet."
      : "No posts were found on this page.";

  return (
    <section className="state">
      <h2>No posts</h2>
      <p>{message}</p>
    </section>
  );
}

function PostListItem({
  filters,
  post,
}: {
  filters: ActiveFilters;
  post: PostSummary;
}) {
  const primary = pickPrimaryLink(post);
  const extraUrls = post.urls.filter((url) => url.id !== primary?.urlId);
  const directHref = safeExternalHref(post.directLink);
  const showDirectLink = directHref !== null && directHref !== primary?.href;
  const title = post.description ?? "Untitled post";
  const absoluteDate = formatPostDate(post.dateCreated);
  const sourceHost = getHostname(post.source);
  // The feed and the article it links to usually live on the same host, so
  // showing both just prints the domain twice. Only worth the space when the
  // post sends you somewhere other than where it came from.
  const targetHost =
    primary?.hostname && primary.hostname !== sourceHost
      ? primary.hostname
      : undefined;

  return (
    <article className="post-item">
      <h2 className="post-title">
        {primary ? (
          <a
            href={primary.href}
            rel="noreferrer"
            target="_blank"
            title={primary.title}
          >
            {title}
          </a>
        ) : (
          title
        )}
      </h2>
      <div className="post-meta">
        <SourceLabel currentQ={filters.q} post={post} />
        <span aria-hidden="true" className="post-meta-separator">
          ·
        </span>
        <time
          className="post-date"
          dateTime={post.dateCreated}
          title={absoluteDate}
        >
          {formatRelative(post.dateCreated) ?? absoluteDate}
        </time>
        {targetHost ? (
          <>
            <span aria-hidden="true" className="post-meta-separator">
              ·
            </span>
            <span className="post-target-host">{targetHost}</span>
          </>
        ) : null}
      </div>
      {extraUrls.length > 0 || showDirectLink ? (
        <div className="post-links">
          {extraUrls.length > 0 ? (
            <ul className="post-link-list" aria-label="Other links in this post">
              {extraUrls.map((url) => (
                <PostLinkRow key={url.id} url={url} />
              ))}
            </ul>
          ) : null}
          {showDirectLink ? (
            <a
              className="post-source-action"
              href={directHref}
              rel="noreferrer"
              target="_blank"
            >
              source
            </a>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

type PrimaryLink = {
  href: string;
  hostname: string | undefined;
  title: string;
  urlId?: number;
};

/**
 * The link the headline should open: the post's first usable URL, falling back
 * to its permalink at the source. Everything else is demoted to the list below,
 * which in practice is almost always empty.
 */
function pickPrimaryLink(post: PostSummary): PrimaryLink | undefined {
  for (const url of post.urls) {
    const href = safeExternalHref(url.href);

    if (href) {
      return {
        href,
        hostname: getHostname(href),
        title: describeUrl(url),
        urlId: url.id,
      };
    }
  }

  const directHref = safeExternalHref(post.directLink);

  return directHref
    ? { href: directHref, hostname: getHostname(directHref), title: directHref }
    : undefined;
}

function describeUrl(url: PostUrl) {
  return url.href !== url.originalUrl
    ? `${url.href} (via ${url.originalUrl})`
    : url.href;
}

function getHostname(value: string | null | undefined): string | undefined {
  const safe = safeExternalHref(value);

  if (!safe) {
    return undefined;
  }

  try {
    return new URL(safe).hostname;
  } catch {
    return undefined;
  }
}

function SourceLabel({
  currentQ,
  post,
}: {
  currentQ: string | undefined;
  post: PostSummary;
}) {
  const descriptor = getSourceDescriptor(post);
  const filterHref = buildSourceFilterHref(descriptor, currentQ);
  const title = descriptor.tooltip ?? post.source;

  const parts = [
    descriptor.typeLabel
      ? { className: "post-source-type", text: descriptor.typeLabel }
      : undefined,
    descriptor.name
      ? { className: "post-source-mid", text: descriptor.name }
      : undefined,
    descriptor.author
      ? { className: "post-source-author", text: descriptor.author }
      : undefined,
  ].filter((part) => part !== undefined);

  const content = parts.map((part, index) => (
    <Fragment key={part.className}>
      {index > 0 ? <span className="post-source-sep">·</span> : null}
      <span className={part.className}>{part.text}</span>
    </Fragment>
  ));

  if (filterHref) {
    return (
      <Link className="post-source" href={filterHref} title={title}>
        {content}
      </Link>
    );
  }

  return (
    <span className="post-source" title={title}>
      {content}
    </span>
  );
}

function PostLinkRow({ url }: { url: PostUrl }) {
  const href = safeExternalHref(url.href);
  const { hostname, pathLabel } = splitUrlForDisplay(url.href);
  const title = describeUrl(url);

  const content = (
    <>
      <span className="post-link-host">{hostname || url.href}</span>
      {pathLabel ? (
        <span className="post-link-path">{pathLabel}</span>
      ) : null}
    </>
  );

  return (
    <li className="post-link-row">
      {href ? (
        <a href={href} rel="noreferrer" target="_blank" title={title}>
          {content}
        </a>
      ) : (
        <span title={title}>{content}</span>
      )}
    </li>
  );
}

function Pagination({ filters, page }: { filters: ActiveFilters; page: PostPage }) {
  const totalPages = Math.max(page.totalPages, 1);

  return (
    <nav className="pagination" aria-label="Posts pagination">
      {page.hasPreviousPage ? (
        <Link href={buildPageHref(page.page - 1, filters)}>Previous</Link>
      ) : (
        <span aria-disabled="true">Previous</span>
      )}
      <span>
        Page {page.page.toLocaleString("en-US")} of {totalPages.toLocaleString("en-US")}
      </span>
      {page.hasNextPage ? (
        <Link href={buildPageHref(page.page + 1, filters)}>Next</Link>
      ) : (
        <span aria-disabled="true">Next</span>
      )}
    </nav>
  );
}

function getFiltersFromSearchParams(
  searchParams: PageSearchParams | undefined,
): ActiveFilters {
  return normalizeFilters({
    source: getSingleSearchParam(searchParams, "source"),
    sourceType: getSingleSearchParam(searchParams, "source_type"),
    author: getSingleSearchParam(searchParams, "author"),
    q: getSingleSearchParam(searchParams, "q"),
  });
}

function getPageFromSearchParams(searchParams: PageSearchParams | undefined) {
  const page = getSingleSearchParam(searchParams, "page");

  if (!page) {
    return 1;
  }

  const parsedPage = Number(page);

  return Number.isInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;
}

function getSingleSearchParam(
  searchParams: PageSearchParams | undefined,
  name: string,
) {
  const value = searchParams?.[name];

  return Array.isArray(value) ? value[0] : value;
}

function buildPageHref(page: number, filters: ActiveFilters) {
  const params = new URLSearchParams();

  if (filters.source) {
    params.set("source", filters.source);
  }

  if (filters.sourceType) {
    params.set("source_type", filters.sourceType);
  }

  if (filters.author) {
    params.set("author", filters.author);
  }

  if (filters.q) {
    params.set("q", filters.q);
  }

  if (page > 1) {
    params.set("page", String(page));
  }

  const query = params.toString();

  return query ? `/?${query}` : "/";
}

type SourceDescriptor = {
  typeLabel?: string;
  name?: string;
  author?: string;
  filter?: {
    sourceType?: SourceType;
    source?: string;
    author?: string;
  };
  tooltip?: string;
};

function getSourceDescriptor(post: PostSummary): SourceDescriptor {
  const author = post.author?.trim() || undefined;

  if (post.sourceType === "reddit") {
    const subreddit = extractRedditSubreddit(post.source);

    return {
      typeLabel: subreddit ? `reddit/${subreddit}` : "reddit",
      author,
      filter: author ? { sourceType: "reddit", author } : undefined,
      tooltip: post.source,
    };
  }

  if (post.sourceType === "bluesky" || post.sourceType === "mastodon") {
    return {
      typeLabel: post.sourceType,
      author,
      filter: author
        ? { sourceType: post.sourceType, author }
        : undefined,
      tooltip: post.source,
    };
  }

  if (post.sourceType === "rss") {
    // A publication name is prose, not a type token, so it renders as a name
    // and the `rss` marker is dropped: the other sources all announce
    // themselves, so an unmarked source is an RSS feed. The feed URL is
    // plumbing a reader never needs, and survives as the tooltip.
    const name = author ?? formatUrlLabel(post.source);

    return {
      name,
      filter: { sourceType: "rss", source: post.source },
      tooltip: post.source ? `${name} — ${post.source}` : name,
    };
  }

  return {
    name: formatUrlLabel(post.source),
    author,
    filter: post.source ? { source: post.source } : undefined,
    tooltip: post.source,
  };
}

function buildSourceFilterHref(
  descriptor: SourceDescriptor,
  currentQ: string | undefined,
) {
  if (!descriptor.filter) {
    return undefined;
  }

  const params = new URLSearchParams();

  if (descriptor.filter.sourceType) {
    params.set("source_type", descriptor.filter.sourceType);
  }

  if (descriptor.filter.source) {
    params.set("source", descriptor.filter.source);
  }

  if (descriptor.filter.author) {
    params.set("author", descriptor.filter.author);
  }

  if (currentQ) {
    params.set("q", currentQ);
  }

  const query = params.toString();

  return query ? `/?${query}` : "/";
}

function extractRedditSubreddit(source: string): string | undefined {
  try {
    const url = new URL(source);
    const match = url.pathname.match(/^\/r\/([^/]+)/i);

    return match?.[1];
  } catch {
    return undefined;
  }
}

function splitUrlForDisplay(value: string): {
  hostname: string;
  pathLabel: string;
} {
  try {
    const url = new URL(value);
    const pathLabel = `${url.pathname}${url.search}${url.hash}`;
    const cleanPath = pathLabel === "/" ? "" : pathLabel;

    return { hostname: url.hostname, pathLabel: cleanPath };
  } catch {
    return { hostname: value, pathLabel: "" };
  }
}

function formatPostDate(value: string) {
  const date = new Date(value);

  return Number.isNaN(date.valueOf()) ? value : dateFormatter.format(date);
}

function formatUrlLabel(value: string) {
  try {
    const url = new URL(value);

    return `${url.hostname}${url.pathname}${url.search}`;
  } catch {
    return value;
  }
}

function normalizeFilters(filters: PostFilters): ActiveFilters {
  const source = normalizeOptionalText(filters.source);
  const sourceType = normalizeSourceType(filters.sourceType);
  const author = normalizeOptionalText(filters.author);
  const q = normalizeOptionalText(filters.q);

  return { source, sourceType, author, q };
}

function normalizeOptionalText(value: string | undefined): string | undefined {
  const text = value?.trim().slice(0, 200);

  return text ? text : undefined;
}

function normalizeSourceType(value: string | undefined): SourceType | undefined {
  const text = value?.trim().toLowerCase();

  return VALID_SOURCE_TYPES.find((t) => t === text);
}

function hasActiveFilters(filters: ActiveFilters) {
  return Boolean(
    filters.source || filters.sourceType || filters.author || filters.q,
  );
}
