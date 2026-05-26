import { DatabaseSync } from "node:sqlite";

import type { PostPage, PostUrl, SourceType } from "../models/read-models";
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
  sourceType?: SourceType;
  author?: string;
  q?: string;
};

type PostRow = {
  id: number;
  source: string;
  sourceType: SourceType | null;
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

export type PostFilters = {
  source?: string;
  sourceType?: string;
  author?: string;
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
        source_type AS sourceType,
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
): PostFilterQuery {
  const clauses: string[] = [];
  const params: SqlParameter[] = [];

  if (filters.source) {
    clauses.push("posts.source = ?");
    params.push(filters.source);
  }

  if (filters.sourceType) {
    clauses.push("posts.source_type = ?");
    params.push(filters.sourceType);
  }

  if (filters.author) {
    clauses.push("posts.author = ?");
    params.push(filters.author);
  }

  if (filters.q) {
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

const VALID_SOURCE_TYPES: readonly SourceType[] = [
  "rss",
  "reddit",
  "bluesky",
  "mastodon",
];

function normalizeSourceType(value: string | undefined): SourceType | undefined {
  const text = value?.trim().toLowerCase();
  return VALID_SOURCE_TYPES.find((t) => t === text);
}

function normalizePostFilters(filters: PostFilters): NormalizedPostFilters {
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

function toWhereSql(clauses: string[]): string {
  return clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
}

function escapeLikeValue(value: string): string {
  return value
    .replaceAll("\\", "\\\\")
    .replaceAll("%", "\\%")
    .replaceAll("_", "\\_");
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