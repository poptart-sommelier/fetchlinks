import Link from "next/link";

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

    return {
      status: "ready",
      page: await getPosts(sql, {
        ...activeFilters,
        page,
        pageSize: POSTS_PER_PAGE,
      }),
      filters: activeFilters,
    };
  } catch {
    return { status: "error" };
  }
}

export function LatestPostsView({ result }: { result: LatestPostsResult }) {
  if (result.status === "error") {
    return (
      <main className="shell">
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
    <main className="shell">
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
          Fetchlinks
        </p>
        <h1>Latest posts</h1>
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
  const directHref = safeExternalHref(post.directLink);

  return (
    <article className="post-item">
      <header className="post-heading">
        <div className="post-meta">
          <SourceLabel currentQ={filters.q} post={post} />
        </div>
        <time className="post-date" dateTime={post.dateCreated}>
          {formatPostDate(post.dateCreated)}
        </time>
      </header>
      <h2>{post.description ?? "Untitled post"}</h2>
      {post.urls.length > 0 || directHref ? (
        <div className="post-links">
          {post.urls.length > 0 ? (
            <ul className="post-link-list" aria-label="Post links">
              {post.urls.map((url) => (
                <PostLinkRow key={url.id} url={url} />
              ))}
            </ul>
          ) : null}
          {directHref ? (
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

  const content = (
    <>
      <span className="post-source-type">{descriptor.typeLabel}</span>
      {descriptor.middle ? (
        <>
          <span className="post-source-sep">·</span>
          <span className="post-source-mid">{descriptor.middle}</span>
        </>
      ) : null}
      {descriptor.author ? (
        <>
          <span className="post-source-sep">·</span>
          <span className="post-source-author">{descriptor.author}</span>
        </>
      ) : null}
    </>
  );

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
  const usesUnshortenedUrl = url.href !== url.originalUrl;
  const title = usesUnshortenedUrl
    ? `${url.href} (via ${url.originalUrl})`
    : url.href;

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
  typeLabel: string;
  middle?: string;
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
    return {
      typeLabel: "rss",
      middle: formatUrlLabel(post.source),
      author,
      filter: { sourceType: "rss", source: post.source },
      tooltip: post.source,
    };
  }

  return {
    typeLabel: formatUrlLabel(post.source),
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
