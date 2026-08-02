import type { PostPage, PostUrl, SourceType } from "../models/read-models";
import { escapeLikeValue, SqlParams, utcIso, type SqlClient } from "./sql";

type CountRow = {
  count: number;
};

type NormalizedPostFilters = {
  source?: string;
  sourceType?: SourceType;
  author?: string;
  q?: string;
};

type PostFilterQuery = {
  clauses: string[];
  params: SqlParams;
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

// `post_id` is a bigint, which both drivers hand back as a string to avoid
// losing precision past 2^53. The read models are numbers and the ids are
// nowhere near that range, so narrow in SQL: PostgreSQL raises "integer out of
// range" if that assumption is ever wrong, which is better than a silently
// truncated id.
const POST_COLUMNS = `
  posts.post_id::int AS id,
  posts.source,
  posts.source_type   AS "sourceType",
  posts.author,
  posts.description,
  posts.direct_link   AS "directLink",
  ${utcIso("posts.posted_at")} AS "dateCreated",
  posts.unique_id     AS "uniqueId"
`;

export async function getPostCount(sql: SqlClient): Promise<number> {
  const rows = await sql.query<CountRow>(
    "SELECT COUNT(*)::int AS count FROM content.posts",
  );

  return rows[0]?.count ?? 0;
}

export async function getPosts(
  sql: SqlClient,
  options: GetPostsOptions = {},
): Promise<PostPage> {
  const page = normalizePositiveInteger(options.page, DEFAULT_PAGE, "page");
  const pageSize = normalizePositiveInteger(
    options.pageSize,
    DEFAULT_PAGE_SIZE,
    "pageSize",
  );
  const filters = normalizePostFilters(options);
  const filterQuery = buildPostFilterQuery(filters);
  const whereSql = toWhereSql(filterQuery.clauses);
  const totalPosts = await getFilteredPostCount(sql, filterQuery, whereSql);
  const totalPages = Math.ceil(totalPosts / pageSize);
  const limit = filterQuery.params.next(pageSize);
  const offset = filterQuery.params.next((page - 1) * pageSize);
  const postRows = await sql.query<PostRow>(
    `
      SELECT ${POST_COLUMNS}
      FROM content.posts posts
      ${whereSql}
      ORDER BY posts.posted_at DESC, posts.post_id DESC
      LIMIT ${limit} OFFSET ${offset}
    `,
    filterQuery.params.toArray(),
  );
  const urlsByPostId = await getUrlsByPostId(
    sql,
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

async function getFilteredPostCount(
  sql: SqlClient,
  filterQuery: PostFilterQuery,
  whereSql: string,
): Promise<number> {
  const rows = await sql.query<CountRow>(
    `
      SELECT COUNT(*)::int AS count
      FROM content.posts posts
      ${whereSql}
    `,
    filterQuery.params.toArray(),
  );

  return rows[0]?.count ?? 0;
}

async function getUrlsByPostId(
  sql: SqlClient,
  postIds: number[],
): Promise<Map<number, PostUrl[]>> {
  if (postIds.length === 0) {
    return new Map();
  }

  const urlRows = await sql.query<PostUrlRow>(
    `
      SELECT
        post_url_id::int   AS id,
        post_id::int       AS "postId",
        position,
        url                AS "originalUrl",
        url_hash           AS "urlHash",
        unshortened_url    AS "unshortenedUrl"
      FROM content.post_urls
      WHERE post_id = ANY($1::bigint[])
      ORDER BY post_id ASC, position ASC, post_url_id ASC
    `,
    [postIds],
  );
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
  const params = new SqlParams();

  if (filters.source) {
    clauses.push(`posts.source = ${params.next(filters.source)}`);
  }

  if (filters.sourceType) {
    clauses.push(`posts.source_type = ${params.next(filters.sourceType)}`);
  }

  if (filters.author) {
    clauses.push(`posts.author = ${params.next(filters.author)}`);
  }

  if (filters.q) {
    const pattern = params.next(`%${escapeLikeValue(filters.q.toLowerCase())}%`);

    clauses.push(`
      (
        LOWER(COALESCE(posts.description, '')) LIKE ${pattern} ESCAPE '\\'
        OR LOWER(posts.source) LIKE ${pattern} ESCAPE '\\'
        OR LOWER(COALESCE(posts.author, '')) LIKE ${pattern} ESCAPE '\\'
        OR LOWER(COALESCE(posts.direct_link, '')) LIKE ${pattern} ESCAPE '\\'
        OR EXISTS (
          SELECT 1
          FROM content.post_urls search_urls
          WHERE search_urls.post_id = posts.post_id
            AND (
              LOWER(search_urls.url) LIKE ${pattern} ESCAPE '\\'
              OR LOWER(COALESCE(search_urls.unshortened_url, '')) LIKE ${pattern} ESCAPE '\\'
            )
        )
      )
    `);
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
