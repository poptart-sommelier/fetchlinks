import type {
  PostOccurrence,
  PostPage,
  PostUrl,
  SourceType,
} from "../models/read-models";
import { escapeLikeValue, SqlParams, utcIso, type SqlClient } from "./sql";

type CountRow = {
  count: number;
};

type NormalizedPostFilters = {
  source?: string;
  sourceType?: SourceType;
  author?: string;
  q?: string;
  uniqueId?: string;
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
  urlHost: string | null;
  muted: boolean;
};

type PostOccurrenceRow = {
  id: number;
  postId: number;
  sourceType: SourceType | null;
  channelKey: string;
  channelLabel: string;
  actorKey: string;
  actorLabel: string;
  source: string;
  directLink: string;
};

export type PostFilters = {
  source?: string;
  sourceType?: string;
  author?: string;
  q?: string;
  /** Exact post identity. Not a reader-facing filter: it is how a mutation
   * re-reads the one post it was asked about. */
  uniqueId?: string;
};

export type GetPostsOptions = PostFilters & {
  /** Owner mode keeps muted posts, origins and links so each decision can be
   * explained and undone. Public reads always leave this false. */
  includeMuted?: boolean;
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
  const includeMuted = options.includeMuted === true;
  const filterQuery = buildPostFilterQuery(filters, includeMuted);
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
  const { mutedDomainsByPostId, urlsByPostId } = await getUrlsByPostId(
    sql,
    postRows.map((post) => post.id),
    includeMuted,
  );
  const occurrencesByPostId = await getOccurrencesByPostId(
    sql,
    postRows.map((post) => post.id),
    includeMuted,
  );

  return {
    posts: postRows.map((post) => {
      const urls = urlsByPostId.get(post.id) ?? [];
      const occurrences = occurrencesByPostId.get(post.id) ?? [];
      const representative = includeMuted ? undefined : occurrences[0];

      return {
        ...post,
        // If the first-arrival origin is muted, name the first origin a public
        // reader can actually see rather than leaking the suppressed one.
        ...(representative
          ? {
              source: representative.source,
              sourceType: representative.sourceType,
              author:
                representative.actorLabel || representative.channelLabel || "",
              directLink: representative.directLink,
            }
          : {}),
        description: includeMuted
          ? post.description
          : redactMutedDomains(
              post.description,
              mutedDomainsByPostId.get(post.id) ?? [],
            ),
        urls,
        occurrences,
      };
    }),
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
  includeMuted: boolean,
): Promise<{
  mutedDomainsByPostId: Map<number, string[]>;
  urlsByPostId: Map<number, PostUrl[]>;
}> {
  if (postIds.length === 0) {
    return {
      mutedDomainsByPostId: new Map(),
      urlsByPostId: new Map(),
    };
  }

  const urlRows = await sql.query<PostUrlRow>(
    `
      SELECT
        post_url_id::int   AS id,
        post_id::int       AS "postId",
        position,
        url                AS "originalUrl",
        url_hash           AS "urlHash",
        unshortened_url    AS "unshortenedUrl",
        url_host           AS "urlHost",
        ${includeMuted ? "FALSE" : urlIsMuted("post_urls")} AS muted
      FROM content.post_urls
      WHERE post_id = ANY($1::bigint[])
      ORDER BY post_id ASC, position ASC, post_url_id ASC
    `,
    [postIds],
  );
  const urlsByPostId = new Map<number, PostUrl[]>();
  const mutedDomainsByPostId = new Map<number, string[]>();

  for (const url of urlRows) {
    if (url.muted) {
      if (url.urlHost) {
        const domains = mutedDomainsByPostId.get(url.postId) ?? [];

        domains.push(url.urlHost);
        mutedDomainsByPostId.set(url.postId, domains);
      }
      continue;
    }

    const postUrls = urlsByPostId.get(url.postId) ?? [];

    postUrls.push({
      ...url,
      urlHost: url.urlHost ?? "",
      href: url.unshortenedUrl ?? url.originalUrl,
    });
    urlsByPostId.set(url.postId, postUrls);
  }

  return { mutedDomainsByPostId, urlsByPostId };
}

/**
 * Every recorded arrival for the given posts. These are what the owner rates:
 * a card offers its feed or subreddit and the account that posted it, and a
 * link that arrived twice offers both origins.
 */
async function getOccurrencesByPostId(
  sql: SqlClient,
  postIds: number[],
  includeMuted: boolean,
): Promise<Map<number, PostOccurrence[]>> {
  if (postIds.length === 0) {
    return new Map();
  }

  const rows = await sql.query<PostOccurrenceRow>(
    `
      SELECT
        occurrence_id::int AS id,
        post_id::int       AS "postId",
        source_type        AS "sourceType",
        channel_key        AS "channelKey",
        channel_label      AS "channelLabel",
        actor_key          AS "actorKey",
        actor_label        AS "actorLabel",
        source,
        direct_link        AS "directLink"
      FROM content.post_occurrences
      WHERE post_id = ANY($1::bigint[])
        ${includeMuted ? "" : `AND ${originIsPublic("post_occurrences")}`}
      ORDER BY post_id ASC, first_seen_at ASC, occurrence_id ASC
    `,
    [postIds],
  );
  const byPostId = new Map<number, PostOccurrence[]>();

  for (const row of rows) {
    const existing = byPostId.get(row.postId) ?? [];

    existing.push(row);
    byPostId.set(row.postId, existing);
  }

  return byPostId;
}

function buildPostFilterQuery(
  filters: NormalizedPostFilters,
  includeMuted: boolean,
): PostFilterQuery {
  const clauses: string[] = [];
  const params = new SqlParams();

  if (!includeMuted) {
    clauses.push(publicPostIsVisible());
  }

  if (filters.source) {
    const source = params.next(filters.source);
    clauses.push(
      includeMuted
        ? `posts.source = ${source}`
        : visibleOriginMatches(
            `filter_origin.source = ${source}`,
            `posts.source = ${source}`,
          ),
    );
  }

  if (filters.sourceType) {
    const sourceType = params.next(filters.sourceType);
    clauses.push(
      includeMuted
        ? `posts.source_type = ${sourceType}`
        : visibleOriginMatches(
            `filter_origin.source_type = ${sourceType}`,
            `posts.source_type = ${sourceType}`,
          ),
    );
  }

  if (filters.author) {
    const author = params.next(filters.author);
    clauses.push(
      includeMuted
        ? `posts.author = ${author}`
        : visibleOriginMatches(
            `(filter_origin.actor_label = ${author}
              OR (
                filter_origin.actor_label = ''
                AND filter_origin.channel_label = ${author}
              ))`,
            `COALESCE(posts.author, '') = ${author}`,
          ),
    );
  }

  if (filters.uniqueId) {
    clauses.push(`posts.unique_id = ${params.next(filters.uniqueId)}`);
  }

  if (filters.q) {
    const pattern = params.next(`%${escapeLikeValue(filters.q.toLowerCase())}%`);
    const descriptionMatches = includeMuted
      ? `LOWER(COALESCE(posts.description, '')) LIKE ${pattern} ESCAPE '\\'`
      : `
        (
          LOWER(COALESCE(posts.description, '')) LIKE ${pattern} ESCAPE '\\'
          AND NOT EXISTS (
            SELECT 1
            FROM content.post_urls muted_description_url
            WHERE muted_description_url.post_id = posts.post_id
              AND ${urlIsMuted("muted_description_url")}
          )
        )
      `;
    const sourceFields = includeMuted
      ? `
        OR LOWER(posts.source) LIKE ${pattern} ESCAPE '\\'
        OR LOWER(COALESCE(posts.author, '')) LIKE ${pattern} ESCAPE '\\'
        OR LOWER(COALESCE(posts.direct_link, '')) LIKE ${pattern} ESCAPE '\\'
      `
      : `
        OR EXISTS (
          SELECT 1
          FROM content.post_occurrences search_origins
          WHERE search_origins.post_id = posts.post_id
            AND ${originIsPublic("search_origins")}
            AND (
              LOWER(search_origins.source) LIKE ${pattern} ESCAPE '\\'
              OR LOWER(search_origins.channel_label) LIKE ${pattern} ESCAPE '\\'
              OR LOWER(search_origins.actor_label) LIKE ${pattern} ESCAPE '\\'
              OR LOWER(search_origins.direct_link) LIKE ${pattern} ESCAPE '\\'
            )
        )
        OR (
          NOT EXISTS (
            SELECT 1
            FROM content.post_occurrences any_search_origin
            WHERE any_search_origin.post_id = posts.post_id
          )
          AND (
            LOWER(posts.source) LIKE ${pattern} ESCAPE '\\'
            OR LOWER(COALESCE(posts.author, '')) LIKE ${pattern} ESCAPE '\\'
            OR LOWER(COALESCE(posts.direct_link, '')) LIKE ${pattern} ESCAPE '\\'
          )
        )
      `;

    clauses.push(`
      (
        ${descriptionMatches}
        ${sourceFields}
        OR EXISTS (
          SELECT 1
          FROM content.post_urls search_urls
          WHERE search_urls.post_id = posts.post_id
            ${includeMuted ? "" : `AND ${urlIsPublic("search_urls")}`}
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

function publicPostIsVisible(): string {
  return `
    NOT EXISTS (
      SELECT 1
      FROM curation.mutes post_mute
      WHERE post_mute.target_type = 'post'
        AND post_mute.target_key = posts.unique_id
    )
    AND (
      NOT EXISTS (
        SELECT 1
        FROM content.post_occurrences any_origin
        WHERE any_origin.post_id = posts.post_id
      )
      OR EXISTS (
        SELECT 1
        FROM content.post_occurrences visible_origin
        WHERE visible_origin.post_id = posts.post_id
          AND ${originIsPublic("visible_origin")}
      )
    )
    AND (
      NOT EXISTS (
        SELECT 1
        FROM content.post_urls any_url
        WHERE any_url.post_id = posts.post_id
      )
      OR EXISTS (
        SELECT 1
        FROM content.post_urls visible_url
        WHERE visible_url.post_id = posts.post_id
          AND ${urlIsPublic("visible_url")}
      )
    )
  `;
}

function visibleOriginMatches(
  occurrencePredicate: string,
  fallbackPredicate: string,
): string {
  return `
    (
      EXISTS (
        SELECT 1
        FROM content.post_occurrences filter_origin
        WHERE filter_origin.post_id = posts.post_id
          AND ${originIsPublic("filter_origin")}
        AND ${occurrencePredicate}
      )
      OR (
        NOT EXISTS (
          SELECT 1
          FROM content.post_occurrences any_filter_origin
          WHERE any_filter_origin.post_id = posts.post_id
        )
        AND ${fallbackPredicate}
      )
    )
  `;
}

function originIsPublic(alias: string): string {
  return `
    (
      ${alias}.source_type IS DISTINCT FROM 'rss'
      OR EXISTS (
        SELECT 1
        FROM catalog.rss_feeds active_feed
        WHERE active_feed.normalized_url = ${alias}.channel_key
          AND active_feed.enabled = true
          AND active_feed.deleted_at IS NULL
      )
    )
    AND (
      ${alias}.source_type IS DISTINCT FROM 'reddit'
      OR EXISTS (
        SELECT 1
        FROM catalog.subreddits active_subreddit
        WHERE active_subreddit.normalized_name = ${alias}.channel_key
          AND active_subreddit.enabled = true
          AND active_subreddit.deleted_at IS NULL
      )
    )
    AND NOT EXISTS (
      SELECT 1
      FROM curation.mutes origin_mute
      WHERE (
        origin_mute.target_type = 'channel'
        AND ${alias}.channel_key <> ''
        AND origin_mute.target_key =
          ${alias}.source_type || chr(31) || ${alias}.channel_key
      )
      OR (
        origin_mute.target_type = 'actor'
        AND ${alias}.actor_key <> ''
        AND origin_mute.target_key =
          ${alias}.source_type || chr(31) || ${alias}.actor_key
      )
    )
  `;
}

function urlIsPublic(alias: string): string {
  return `NOT (${urlIsMuted(alias)})`;
}

function urlIsMuted(alias: string): string {
  return `
    EXISTS (
      SELECT 1
      FROM curation.mutes domain_mute
      WHERE domain_mute.target_type = 'domain'
        AND domain_mute.target_key = ${alias}.url_host
    )
  `;
}

function redactMutedDomains(
  description: string | null,
  mutedDomains: readonly string[],
): string | null {
  if (!description || mutedDomains.length === 0) {
    return description;
  }

  let redacted = description;

  for (const domain of [...new Set(mutedDomains)].sort(
    (left, right) => right.length - left.length,
  )) {
    const escaped = domain.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const domainUrl = new RegExp(
      `(^|[^a-z0-9.-])(?:https?://)?(?:www\\.)?${escaped}(?::\\d+)?(?:[/?#][^\\s<>"']*)?`,
      "gim",
    );

    redacted = redacted.replace(domainUrl, "$1");
  }

  return redacted.replace(/[ \t]{2,}/g, " ").trim();
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
  const uniqueId = normalizeOptionalText(filters.uniqueId);

  return { source, sourceType, author, q, uniqueId };
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
