import Link from "next/link";

import type {
  DomainSummary,
  PostPage,
  PostSummary,
  PostUrl,
  SourceSummary,
} from "../models/read-models";
import {
  getDomainSummaries,
  getPosts,
  getSourceSummaries,
  openConfiguredFetchlinksDatabase,
  type PostFilters,
} from "../server/db";

type PageSearchParams = Record<string, string | string[] | undefined>;

type HomeProps = {
  searchParams?: Promise<PageSearchParams>;
};

type Env = Partial<Record<string, string | undefined>>;

type ActiveFilters = {
  source?: string;
  domain?: string;
  q?: string;
};

type LatestPostsResult =
  | {
      status: "ready";
      page: PostPage;
      sources: SourceSummary[];
      domains: DomainSummary[];
      filters: ActiveFilters;
    }
  | {
      status: "error";
    };

const POSTS_PER_PAGE = 50;

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

  return <LatestPostsView result={loadLatestPosts({ page, filters })} />;
}

export function loadLatestPosts({
  env = process.env,
  filters = {},
  page = 1,
}: {
  env?: Env;
  filters?: PostFilters;
  page?: number;
} = {}): LatestPostsResult {
  const activeFilters = normalizeFilters(filters);

  try {
    const database = openConfiguredFetchlinksDatabase(env);

    try {
      return {
        status: "ready",
        page: getPosts(database, {
          ...activeFilters,
          page,
          pageSize: POSTS_PER_PAGE,
        }),
        sources: getSourceSummaries(database, {
          domain: activeFilters.domain,
          q: activeFilters.q,
        }),
        domains: getDomainSummaries(database, {
          source: activeFilters.source,
          q: activeFilters.q,
        }),
        filters: activeFilters,
      };
    } finally {
      if (database.isOpen) {
        database.close();
      }
    }
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
      <FilterBar
        domains={result.domains}
        filters={result.filters}
        sources={result.sources}
      />
      {page.posts.length === 0 ? (
        <EmptyPostsState filters={result.filters} page={page} />
      ) : null}
      {page.posts.length > 0 ? (
        <section className="post-list" aria-label="Latest posts">
          {page.posts.map((post) => (
            <PostListItem key={post.id} post={post} />
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
        <p className="eyebrow">Fetchlinks</p>
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

function FilterBar({
  domains,
  filters,
  sources,
}: {
  domains: DomainSummary[];
  filters: ActiveFilters;
  sources: SourceSummary[];
}) {
  const sourceOptions = includeActiveSource(sources, filters.source);
  const domainOptions = includeActiveDomain(domains, filters.domain);

  return (
    <form action="/" aria-label="Filter posts" className="filter-bar" method="get">
      <label>
        <span>Search</span>
        <input
          defaultValue={filters.q ?? ""}
          name="q"
          placeholder="Description, author, URL"
          type="search"
        />
      </label>
      <label>
        <span>Source</span>
        <select defaultValue={filters.source ?? ""} name="source">
          <option value="">Any source</option>
          {sourceOptions.map((source) => (
            <option key={source.source} value={source.source}>
              {source.source} ({source.postCount.toLocaleString("en-US")})
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Domain</span>
        <select defaultValue={filters.domain ?? ""} name="domain">
          <option value="">Any domain</option>
          {domainOptions.map((domain) => (
            <option key={domain.domain} value={domain.domain}>
              {domain.domain} ({domain.postCount.toLocaleString("en-US")})
            </option>
          ))}
        </select>
      </label>
      <div className="filter-actions">
        <button type="submit">Apply</button>
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

function PostListItem({ post }: { post: PostSummary }) {
  return (
    <article className="post-item">
      <header className="post-heading">
        <div className="post-meta">
          <a
            className="post-source"
            href={toExternalHref(post.source)}
            rel="noreferrer"
            target="_blank"
            title={post.source}
          >
            {getSourceLabel(post)}
          </a>
          <span aria-hidden="true" className="post-meta-separator">
            /
          </span>
          <time dateTime={post.dateCreated}>{formatPostDate(post.dateCreated)}</time>
        </div>
      </header>
      <h2>{post.description ?? "Untitled post"}</h2>
      {post.urls.length > 0 || post.directLink ? (
        <nav aria-label="Post links" className="post-links">
          {post.urls.length > 0 ? (
            <span className="post-url-actions">
              {post.urls.map((url, index) => (
                <PostLinkAction key={url.id} showSeparator={index > 0}>
                  <PostUrlItem label={`link ${index + 1}`} url={url} />
                </PostLinkAction>
              ))}
            </span>
          ) : null}
          {post.directLink ? (
            <a
              className="post-source-action"
              href={post.directLink}
              rel="noreferrer"
              target="_blank"
            >
              source
            </a>
          ) : null}
        </nav>
      ) : null}
    </article>
  );
}

function PostLinkAction({
  children,
  showSeparator,
}: {
  children: React.ReactNode;
  showSeparator: boolean;
}) {
  return (
    <span className="post-link-action">
      {showSeparator ? <span className="post-link-separator">,</span> : null}
      {children}
    </span>
  );
}

function PostUrlItem({ label, url }: { label: string; url: PostUrl }) {
  const usesUnshortenedUrl = url.href !== url.originalUrl;

  return (
    <a
      href={url.href}
      rel="noreferrer"
      target="_blank"
      title={usesUnshortenedUrl ? `via ${formatUrlLabel(url.originalUrl)}` : url.href}
    >
      {label}
    </a>
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
    domain: getSingleSearchParam(searchParams, "domain"),
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

  if (filters.domain) {
    params.set("domain", filters.domain);
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

function getSourceLabel(post: PostSummary) {
  return post.author?.trim() || formatUrlLabel(post.source);
}

function toExternalHref(value: string) {
  try {
    return new URL(value).toString();
  } catch {
    return `https://${value}`;
  }
}

function normalizeFilters(filters: PostFilters): ActiveFilters {
  const source = normalizeOptionalText(filters.source);
  const domain = normalizeOptionalText(filters.domain)?.toLowerCase();
  const q = normalizeOptionalText(filters.q);

  return { source, domain, q };
}

function normalizeOptionalText(value: string | undefined): string | undefined {
  const text = value?.trim();

  return text ? text : undefined;
}

function hasActiveFilters(filters: ActiveFilters) {
  return Boolean(filters.source || filters.domain || filters.q);
}

function includeActiveSource(
  sources: SourceSummary[],
  activeSource: string | undefined,
): SourceSummary[] {
  if (!activeSource || sources.some((source) => source.source === activeSource)) {
    return sources;
  }

  return [{ source: activeSource, postCount: 0, latestPostDate: null }, ...sources];
}

function includeActiveDomain(
  domains: DomainSummary[],
  activeDomain: string | undefined,
): DomainSummary[] {
  if (!activeDomain || domains.some((domain) => domain.domain === activeDomain)) {
    return domains;
  }

  return [
    { domain: activeDomain, postCount: 0, urlCount: 0, latestPostDate: null },
    ...domains,
  ];
}
