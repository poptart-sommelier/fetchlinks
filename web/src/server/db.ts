import { DatabaseSync } from "node:sqlite";

import type {
  DomainSummary,
  PostPage,
  PostUrl,
  SourceSummary,
} from "../models/read-models";
import { loadAppConfig, type AppConfig } from "./config";

type DbConfig = Pick<AppConfig, "fetchlinksDbPath">;

type Env = Partial<Record<string, string | undefined>>;

type CountRow = {
  count: number;
};

type SqlParameter = number | string;

type PostFilterQuery = {
  clauses: string[];
  params: SqlParameter[];
};

type NormalizedPostFilters = {
  source?: string;
  domain?: string;
  q?: string;
};

type PostRow = {
  id: number;
  source: string;
  author: string | null;
  description: string | null;
  directLink: string | null;
  dateCreated: string;
  uniqueId: string;
};

type PostUrlRow = {
  id: number;
  postId: number;
  position: number;
  originalUrl: string;
  urlHash: string;
  unshortenedUrl: string | null;
};

type SourceSummaryRow = {
  source: string;
  postCount: number;
  latestPostDate: string | null;
};

type DomainSummaryRow = {
  domain: string;
  postCount: number;
  urlCount: number;
  latestPostDate: string | null;
};

export type PostFilters = {
  source?: string;
  domain?: string;
  q?: string;
};

export type GetPostsOptions = PostFilters & {
  page?: number;
  pageSize?: number;
};

const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 50;

export type FetchlinksDatabase = DatabaseSync;

export function openFetchlinksDatabase(config: DbConfig): FetchlinksDatabase {
  return new DatabaseSync(config.fetchlinksDbPath, {
    readOnly: true,
    timeout: 5000,
  });
}

export function openConfiguredFetchlinksDatabase(
  env: Env = process.env,
): FetchlinksDatabase {
  return openFetchlinksDatabase(loadAppConfig(env));
}

export function withFetchlinksDatabase<T>(
  config: DbConfig,
  callback: (database: FetchlinksDatabase) => T,
): T {
  const database = openFetchlinksDatabase(config);

  try {
    return callback(database);
  } finally {
    if (database.isOpen) {
      database.close();
    }
  }
}

export function getPostCount(database: FetchlinksDatabase): number {
  const row = database.prepare("SELECT COUNT(*) AS count FROM posts").get() as
    | CountRow
    | undefined;

  return row?.count ?? 0;
}

export function getPosts(
  database: FetchlinksDatabase,
  options: GetPostsOptions = {},
): PostPage {
  const page = normalizePositiveInteger(options.page, DEFAULT_PAGE, "page");
  const pageSize = normalizePositiveInteger(
    options.pageSize,
    DEFAULT_PAGE_SIZE,
    "pageSize",
  );
  const filters = normalizePostFilters(options);
  const filterQuery = buildPostFilterQuery(filters);
  const whereSql = toWhereSql(filterQuery.clauses);
  const totalPosts = getFilteredPostCount(database, filterQuery);
  const totalPages = Math.ceil(totalPosts / pageSize);
  const postRows = database
    .prepare(`
      SELECT
        idx AS id,
        source,
        author,
        description,
        direct_link AS directLink,
        date_created AS dateCreated,
        unique_id_string AS uniqueId
      FROM posts
      ${whereSql}
      ORDER BY date_created DESC, idx DESC
      LIMIT ? OFFSET ?
    `)
    .all(...filterQuery.params, pageSize, (page - 1) * pageSize) as PostRow[];
  const urlsByPostId = getUrlsByPostId(
    database,
    postRows.map((post) => post.id),
  );

  return {
    posts: postRows.map((post) => ({
      ...post,
      urls: urlsByPostId.get(post.id) ?? [],
    })),
    page,
    pageSize,
    totalPosts,
    totalPages,
    hasPreviousPage: page > 1,
    hasNextPage: page < totalPages,
  };
}

export function getSourceSummaries(
  database: FetchlinksDatabase,
  filters: PostFilters = {},
): SourceSummary[] {
  const filterQuery = buildPostFilterQuery(normalizePostFilters(filters));
  const rows = database
    .prepare(`
      SELECT
        source,
        COUNT(*) AS postCount,
        MAX(date_created) AS latestPostDate
      FROM posts
      ${toWhereSql(filterQuery.clauses)}
      GROUP BY source
      ORDER BY postCount DESC, source ASC
    `)
    .all(...filterQuery.params) as SourceSummaryRow[];

  return rows;
}

export function getDomainSummaries(
  database: FetchlinksDatabase,
  filters: PostFilters = {},
): DomainSummary[] {
  const normalizedFilters = normalizePostFilters(filters);
  const postFilterQuery = buildPostFilterQuery(normalizedFilters, {
    includeDomain: false,
  });
  const clauses = ["url_domains.domain <> ''", ...postFilterQuery.clauses];
  const params = [...postFilterQuery.params];

  if (normalizedFilters.domain) {
    clauses.push("url_domains.domain = ?");
    params.push(normalizedFilters.domain);
  }

  const rows = database
    .prepare(`
      WITH url_domains AS (
        SELECT
          post_id AS postId,
          ${domainSqlExpression("post_urls")} AS domain
        FROM post_urls
      )
      SELECT
        url_domains.domain,
        COUNT(DISTINCT url_domains.postId) AS postCount,
        COUNT(*) AS urlCount,
        MAX(posts.date_created) AS latestPostDate
      FROM url_domains
      INNER JOIN posts ON posts.idx = url_domains.postId
      ${toWhereSql(clauses)}
      GROUP BY url_domains.domain
      ORDER BY postCount DESC, urlCount DESC, url_domains.domain ASC
    `)
    .all(...params) as DomainSummaryRow[];

  return rows;
}

function getFilteredPostCount(
  database: FetchlinksDatabase,
  filterQuery: PostFilterQuery,
): number {
  const row = database
    .prepare(`
      SELECT COUNT(*) AS count
      FROM posts
      ${toWhereSql(filterQuery.clauses)}
    `)
    .get(...filterQuery.params) as CountRow | undefined;

  return row?.count ?? 0;
}

function getUrlsByPostId(
  database: FetchlinksDatabase,
  postIds: number[],
): Map<number, PostUrl[]> {
  if (postIds.length === 0) {
    return new Map();
  }

  const placeholders = postIds.map(() => "?").join(", ");
  const urlRows = database
    .prepare(`
      SELECT
        idx AS id,
        post_id AS postId,
        position,
        url AS originalUrl,
        url_hash AS urlHash,
        unshortened_url AS unshortenedUrl
      FROM post_urls
      WHERE post_id IN (${placeholders})
      ORDER BY post_id ASC, position ASC, idx ASC
    `)
    .all(...postIds) as PostUrlRow[];
  const urlsByPostId = new Map<number, PostUrl[]>();

  for (const url of urlRows) {
    const postUrls = urlsByPostId.get(url.postId) ?? [];

    postUrls.push({
      ...url,
      href: url.unshortenedUrl ?? url.originalUrl,
    });
    urlsByPostId.set(url.postId, postUrls);
  }

  return urlsByPostId;
}

function buildPostFilterQuery(
  filters: NormalizedPostFilters,
  {
    includeSource = true,
    includeDomain = true,
    includeSearch = true,
  }: {
    includeSource?: boolean;
    includeDomain?: boolean;
    includeSearch?: boolean;
  } = {},
): PostFilterQuery {
  const clauses: string[] = [];
  const params: SqlParameter[] = [];

  if (includeSource && filters.source) {
    clauses.push("posts.source = ?");
    params.push(filters.source);
  }

  if (includeDomain && filters.domain) {
    clauses.push(`
      EXISTS (
        SELECT 1
        FROM post_urls domain_urls
        WHERE domain_urls.post_id = posts.idx
          AND ${domainSqlExpression("domain_urls")} = ?
      )
    `);
    params.push(filters.domain);
  }

  if (includeSearch && filters.q) {
    const pattern = `%${escapeLikeValue(filters.q.toLowerCase())}%`;

    clauses.push(`
      (
        LOWER(COALESCE(posts.description, '')) LIKE ? ESCAPE '\\'
        OR LOWER(posts.source) LIKE ? ESCAPE '\\'
        OR LOWER(COALESCE(posts.author, '')) LIKE ? ESCAPE '\\'
        OR LOWER(COALESCE(posts.direct_link, '')) LIKE ? ESCAPE '\\'
        OR EXISTS (
          SELECT 1
          FROM post_urls search_urls
          WHERE search_urls.post_id = posts.idx
            AND (
              LOWER(search_urls.url) LIKE ? ESCAPE '\\'
              OR LOWER(COALESCE(search_urls.unshortened_url, '')) LIKE ? ESCAPE '\\'
            )
        )
      )
    `);
    params.push(pattern, pattern, pattern, pattern, pattern, pattern);
  }

  return { clauses, params };
}

function normalizePostFilters(filters: PostFilters): NormalizedPostFilters {
  const source = normalizeOptionalText(filters.source);
  const domain = normalizeOptionalText(filters.domain)?.toLowerCase();
  const q = normalizeOptionalText(filters.q);

  return { source, domain, q };
}

function normalizeOptionalText(value: string | undefined): string | undefined {
  const text = value?.trim().slice(0, 200);

  return text ? text : undefined;
}

function toWhereSql(clauses: string[]): string {
  return clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
}

function escapeLikeValue(value: string): string {
  return value
    .replaceAll("\\", "\\\\")
    .replaceAll("%", "\\%")
    .replaceAll("_", "\\_");
}

function domainSqlExpression(tableAlias: string): string {
  const href = `COALESCE(${tableAlias}.unshortened_url, ${tableAlias}.url)`;
  const withoutScheme = `CASE WHEN INSTR(${href}, '://') > 0 THEN SUBSTR(${href}, INSTR(${href}, '://') + 3) ELSE ${href} END`;
  const beforePath = `CASE WHEN INSTR(${withoutScheme}, '/') > 0 THEN SUBSTR(${withoutScheme}, 1, INSTR(${withoutScheme}, '/') - 1) ELSE ${withoutScheme} END`;
  const beforeQuery = `CASE WHEN INSTR(${beforePath}, '?') > 0 THEN SUBSTR(${beforePath}, 1, INSTR(${beforePath}, '?') - 1) ELSE ${beforePath} END`;

  return `LOWER(CASE WHEN INSTR(${beforeQuery}, '#') > 0 THEN SUBSTR(${beforeQuery}, 1, INSTR(${beforeQuery}, '#') - 1) ELSE ${beforeQuery} END)`;
}

function normalizePositiveInteger(
  value: number | undefined,
  fallback: number,
  name: string,
): number {
  if (value === undefined) {
    return fallback;
  }

  if (!Number.isInteger(value) || value < 1) {
    throw new RangeError(`${name} must be a positive integer.`);
  }

  return value;
}